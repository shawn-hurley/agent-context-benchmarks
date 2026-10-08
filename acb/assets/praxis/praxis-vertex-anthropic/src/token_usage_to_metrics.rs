//! Praxis filter: `token_usage_to_metrics`
//!
//! Reads token usage from filter_metadata (written by the `token_count` filter)
//! and writes benchmark metrics to JSONL file with request_id deduplication.
//!
//! This filter runs AFTER `benchmark_metrics` filter in the chain, so it has
//! access to both token_count's extracted tokens (in metadata) and context data
//! like status, duration, etc.

use crate::metrics_collector::MetricsData;
use async_trait::async_trait;
use bytes::Bytes;
use lazy_static::lazy_static;
use praxis_filter::{
    BodyAccess, BodyMode, FilterAction, FilterError, HttpFilter, HttpFilterContext,
};
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

/// Classification of request content type
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ContentType {
    SystemPrompt,
    UserMessage,
    ToolCall,
    ToolResult,
    AssistantContinuation,
    Mixed,
    Unknown,
}

/// Request classification stored in extensions
#[derive(Clone, Debug)]
pub struct RequestClassification {
    pub content_type: ContentType,
    pub tools: Vec<ToolIdentity>,
    pub tool_count: Option<u32>,
    pub message_count: Option<u32>,
}

/// A normalized tool identity extracted from an OpenAI or Anthropic request.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct ToolIdentity {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub call_id: Option<String>,
}

/// Tool calls observed in the model response for the current request.
#[derive(Clone, Debug, Default)]
pub struct ResponseToolCalls(pub Vec<ToolIdentity>);

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

    // Content classification fields
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content_type: Option<ContentType>,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub tools: Vec<ToolIdentity>,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub tool_results: Vec<ToolIdentity>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_count: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message_count: Option<u32>,
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

    fn merge_tool_calls(
        request_type: Option<ContentType>,
        request_tools: Vec<ToolIdentity>,
        response_tools: Vec<ToolIdentity>,
    ) -> (Option<ContentType>, Vec<ToolIdentity>, Vec<ToolIdentity>) {
        if !response_tools.is_empty() {
            let tool_results = if request_type == Some(ContentType::ToolResult) {
                request_tools
            } else {
                Vec::new()
            };
            (Some(ContentType::ToolCall), response_tools, tool_results)
        } else if request_type == Some(ContentType::ToolResult) {
            (request_type, Vec::new(), request_tools)
        } else {
            (request_type, request_tools, Vec::new())
        }
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
    fn write_metric_if_unique(
        &self,
        metric: &BenchmarkMetric,
    ) -> Result<(), Box<dyn std::error::Error>> {
        if Self::request_id_exists(&metric.request_id) {
            debug!(request_id = %metric.request_id, "request already in metrics file, skipping");
            return Ok(());
        }

        let json = serde_json::to_string(metric)?;
        let mut file = METRICS_FILE
            .lock()
            .map_err(|e| format!("lock failed: {e}"))?;
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

    async fn on_request(
        &self,
        _ctx: &mut HttpFilterContext<'_>,
    ) -> Result<FilterAction, FilterError> {
        Ok(FilterAction::Continue)
    }

    async fn on_response(
        &self,
        _ctx: &mut HttpFilterContext<'_>,
    ) -> Result<FilterAction, FilterError> {
        // Defer metric writing until benchmark_metrics has completed the body.
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
        let responses_complete = ctx
            .extensions
            .get::<MetricsData>()
            .map(|data| data.responses_complete)
            .unwrap_or(false);
        if !end_of_stream && !responses_complete {
            return Ok(FilterAction::Continue);
        }
        // The existing request-ID deduplication prevents a second row if the
        // transport subsequently closes normally after response.completed.

        // Extract all available data from context
        let request_id = ctx.request_id().unwrap_or("-").to_string();
        let endpoint = ctx.request.uri.path().to_string();
        let status_code = ctx
            .extensions
            .get::<MetricsData>()
            .map(|data| data.status_code)
            .unwrap_or(0);
        let request_body_bytes = ctx.request_body_bytes as usize;
        let response_body_bytes = ctx.response_body_bytes as usize;
        let duration_ms = ctx.request_start.elapsed().as_millis() as u64;
        let timestamp_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        // Extract tokens and cache info from metadata (written by token_count filter or benchmark_metrics)
        let (
            input_tokens,
            output_tokens,
            total_tokens,
            cache_read_input_tokens,
            cache_creation_input_tokens,
        ) = Self::extract_tokens_from_metadata(ctx);

        // Extract classification from extensions (written during request phase by request_classifier)
        let response_tools = ctx
            .extensions
            .get::<ResponseToolCalls>()
            .map(|calls| calls.0.clone())
            .unwrap_or_default();
        let (content_type, request_tools, tool_count, message_count) =
            if let Some(classification) = ctx.extensions.get::<RequestClassification>() {
                (
                    Some(classification.content_type.clone()),
                    classification.tools.clone(),
                    classification.tool_count,
                    classification.message_count,
                )
            } else {
                (None, Vec::new(), None, None)
            };
        let (content_type, tools, tool_results) =
            Self::merge_tool_calls(content_type, request_tools, response_tools);
        let tool_count = if !tools.is_empty() {
            Some(tools.len() as u32)
        } else {
            tool_count
        };

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
            content_type,
            tools,
            tool_results,
            tool_count,
            message_count,
        };

        // Write to file with deduplication
        if let Err(e) = self.write_metric_if_unique(&metric) {
            warn!(error = %e, "failed to write metric");
        }

        Ok(FilterAction::Continue)
    }
}

#[cfg(test)]
mod tests {
    use super::{ContentType, TokenUsageToMetricsFilter, ToolIdentity};

    fn tool(name: &str, id: &str) -> ToolIdentity {
        ToolIdentity {
            name: name.to_string(),
            detail: None,
            call_id: Some(id.to_string()),
        }
    }

    #[test]
    fn tool_results_stay_results_until_the_response_calls_another_tool() {
        let result = tool("Bash", "call-1");
        let (kind, calls, results) = TokenUsageToMetricsFilter::merge_tool_calls(
            Some(ContentType::ToolResult),
            vec![result.clone()],
            Vec::new(),
        );
        assert_eq!(kind, Some(ContentType::ToolResult));
        assert!(calls.is_empty());
        assert_eq!(results, vec![result.clone()]);

        let next_call = tool("Task", "call-2");
        let (kind, calls, results) = TokenUsageToMetricsFilter::merge_tool_calls(
            Some(ContentType::ToolResult),
            vec![result.clone()],
            vec![next_call.clone()],
        );
        assert_eq!(kind, Some(ContentType::ToolCall));
        assert_eq!(calls, vec![next_call]);
        assert_eq!(results, vec![result]);
    }

    #[test]
    fn prompt_tools_are_not_recorded_as_tool_results() {
        let response_call = tool("Bash", "call-1");
        let (kind, calls, results) = TokenUsageToMetricsFilter::merge_tool_calls(
            Some(ContentType::SystemPrompt),
            Vec::new(),
            vec![response_call.clone()],
        );
        assert_eq!(kind, Some(ContentType::ToolCall));
        assert_eq!(calls, vec![response_call]);
        assert!(results.is_empty());
    }
}
