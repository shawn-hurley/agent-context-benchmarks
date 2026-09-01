//! Praxis filter: `token_usage_to_metrics`
//!
//! Reads token usage from filter_metadata (written by the `token_count` filter)
//! and writes benchmark metrics to JSONL file with request_id deduplication.
//!
//! This filter runs AFTER `benchmark_metrics` filter in the chain, so it has
//! access to both token_count's extracted tokens (in metadata) and context data
//! like status, duration, etc.

use async_trait::async_trait;
use bytes::Bytes;
use lazy_static::lazy_static;
use praxis_filter::{BodyAccess, BodyMode, FilterAction, FilterError, HttpFilter, HttpFilterContext};
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::{debug, trace, warn};

const METRICS_FILE_PATH: &str = "/tmp/benchmark_metrics.jsonl";

/// Metadata keys written by the token_count filter from research-llm-cost
/// and by the benchmark_metrics filter.
const META_TOKEN_INPUT: &str = "token.input";
const META_TOKEN_OUTPUT: &str = "token.output";
const META_TOKEN_TOTAL: &str = "token.total";
const META_TOKEN_CACHE_READ: &str = "token.cache_read";
const META_TOKEN_CACHE_CREATION: &str = "token.cache_creation";

lazy_static! {
    static ref METRICS_FILE: Mutex<BufWriter<File>> = {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(METRICS_FILE_PATH)
            .expect("failed to open metrics file");
        Mutex::new(BufWriter::new(file))
    };
}

/// Benchmark metric record written to JSONL file.
#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct BenchmarkMetric {
    pub request_id: String,
    pub timestamp_ms: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_read_input_tokens: u64,
    pub cache_creation_input_tokens: u64,
    pub total_tokens: u64,
    pub duration_ms: u64,
    pub status_code: u16,
    pub endpoint: String,
    pub request_body_bytes: usize,
    pub response_body_bytes: usize,
}

/// Filter that reads token usage from filter_metadata and writes metrics to file.
pub struct TokenUsageToMetricsFilter;

impl TokenUsageToMetricsFilter {
    /// Create filter from YAML config.
    pub fn from_config(config: &serde_yaml::Value) -> Result<Box<dyn HttpFilter>, FilterError> {
        // Accepts empty config
        let _: serde_yaml::Value = config.clone();
        Ok(Box::new(Self))
    }

    /// Check if request_id already exists in the metrics file.
    fn request_id_exists(request_id: &str) -> bool {
        match File::open(METRICS_FILE_PATH) {
            Ok(file) => {
                let reader = BufReader::new(file);
                for line in reader.lines() {
                    if let Ok(line) = line {
                        if line.contains(&format!(r#""request_id":"{}""#, request_id)) {
                            return true;
                        }
                    }
                }
                false
            }
            Err(_) => false, // File doesn't exist yet, so ID can't be in it
        }
    }

    /// Extract token counts from filter_metadata.
    /// Returns (input_tokens, output_tokens, total_tokens, cache_read_tokens, cache_creation_tokens).
    fn extract_tokens_from_metadata(ctx: &HttpFilterContext<'_>) -> (u64, u64, u64, u64, u64) {
        let input = ctx
            .get_metadata(META_TOKEN_INPUT)
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(0);

        let output = ctx
            .get_metadata(META_TOKEN_OUTPUT)
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(0);

        let total = ctx
            .get_metadata(META_TOKEN_TOTAL)
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or_else(|| input.saturating_add(output));

        let cache_read = ctx
            .get_metadata(META_TOKEN_CACHE_READ)
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(0);

        let cache_creation = ctx
            .get_metadata(META_TOKEN_CACHE_CREATION)
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(0);

        (input, output, total, cache_read, cache_creation)
    }

    /// Write metric to file if request_id is not already present.
    fn write_metric_if_unique(&self, metric: &BenchmarkMetric) -> Result<(), Box<dyn std::error::Error>> {
        if Self::request_id_exists(&metric.request_id) {
            debug!(request_id = %metric.request_id, "request already in metrics file, skipping");
            return Ok(());
        }

        let json = serde_json::to_string(metric)?;
        let mut file = METRICS_FILE.lock().map_err(|e| format!("lock failed: {e}"))?;
        writeln!(file, "{}", json)?;
        file.flush()?;

        trace!(request_id = %metric.request_id, "wrote metric to file");
        Ok(())
    }
}

#[async_trait]
impl HttpFilter for TokenUsageToMetricsFilter {
    fn name(&self) -> &'static str {
        "token_usage_to_metrics"
    }

    fn response_body_access(&self) -> BodyAccess {
        BodyAccess::ReadOnly
    }

    fn response_body_mode(&self) -> BodyMode {
        BodyMode::Stream
    }

    async fn on_request(&self, _ctx: &mut HttpFilterContext<'_>) -> Result<FilterAction, FilterError> {
        Ok(FilterAction::Continue)
    }

    async fn on_response(&self, _ctx: &mut HttpFilterContext<'_>) -> Result<FilterAction, FilterError> {
        // Defer all metric writing to on_response_body at end_of_stream,
        // when token metadata has been populated by benchmark_metrics or token_count.
        Ok(FilterAction::Continue)
    }

    fn on_response_body(
        &self,
        ctx: &mut HttpFilterContext<'_>,
        _body: &mut Option<Bytes>,
        end_of_stream: bool,
    ) -> Result<FilterAction, FilterError> {
        // Only write the metric once the entire response body has streamed and
        // all metadata (token counts, cache info) has been populated by upstream filters.
        if !end_of_stream {
            return Ok(FilterAction::Continue);
        }

        // Extract all available data from context
        let request_id = ctx.request_id().unwrap_or("-").to_string();
        let endpoint = ctx.request.uri.path().to_string();
        let status_code = ctx
            .response_header
            .as_ref()
            .map(|h| h.status.as_u16())
            .unwrap_or(0);
        let request_body_bytes = ctx.request_body_bytes as usize;
        let response_body_bytes = ctx.response_body_bytes as usize;
        let duration_ms = ctx.request_start.elapsed().as_millis() as u64;
        let timestamp_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        // Extract tokens and cache info from metadata (written by token_count filter or benchmark_metrics)
        let (input_tokens, output_tokens, total_tokens, cache_read_input_tokens, cache_creation_input_tokens) =
            Self::extract_tokens_from_metadata(ctx);

        // Build metric record
        let metric = BenchmarkMetric {
            request_id,
            timestamp_ms,
            input_tokens,
            output_tokens,
            cache_read_input_tokens,
            cache_creation_input_tokens,
            total_tokens,
            duration_ms,
            status_code,
            endpoint,
            request_body_bytes,
            response_body_bytes,
        };

        // Write to file with deduplication
        if let Err(e) = self.write_metric_if_unique(&metric) {
            warn!(error = %e, "failed to write metric");
        }

        Ok(FilterAction::Continue)
    }
}
