//! Praxis filter: `benchmark_metrics`
//!
//! Collects token usage metrics from all inference backends (Vertex AI,
//! vLLM, OpenAI-compatible servers) and writes structured records to a
//! JSONL file for consumption by benchmarking tools.
//!
//! # Mode: Stream + ReadWrite
//!
//! The filter uses `BodyMode::Stream` (the default) and `BodyAccess::ReadWrite`.
//! Chunks flow through to the harness in real time. Token state is accumulated
//! across chunk calls via `ctx.extensions` and the metric is written at
//! `end_of_stream`.
//!
//! For SSE streams, `vertex_event` events are **stripped** from the outgoing
//! body. The Vercel AI SDK (used by opencode) performs strict Zod type
//! validation on every SSE event against a discriminated union; `vertex_event`
//! is not in the schema and causes an `AI_TypeValidationError` crash. Stripping
//! it here fixes opencode without affecting other harnesses.
//!
//! A `sse_partial` byte buffer in `MetricsData` carries incomplete event bytes
//! across chunk boundaries so events that straddle a TCP chunk are handled
//! correctly — partial `vertex_event` bytes are held until the full event
//! arrives and can be identified and dropped.
//!
//! # Token sources (in priority order)
//!
//! 1. SSE `message_start` event                    → input + cache tokens
//! 2. SSE `vertex_event` (stripped from output)    → all token types
//! 3. SSE `message_delta` event                    → output tokens (cumulative)
//! 4. Vertex `x-vertex-ai-*` response headers      → cache tokens (belt-and-suspenders)
//! 5. Vertex `internal-*` response headers         → input + output tokens (fallback)
//! 6. JSON body `usage` field                      → all token types (non-streaming)
//!
//! # Output Format
//!
//! Writes JSONL to `/tmp/benchmark_metrics.jsonl`:
//!
//! ```json
//! {
//!   "request_id": "abc123",
//!   "timestamp_ms": 1693036906983,
//!   "input_tokens": 11,
//!   "output_tokens": 9,
//!   "cache_read_input_tokens": 0,
//!   "cache_creation_input_tokens": 0,
//!   "total_tokens": 20,
//!   "duration_ms": 578,
//!   "status_code": 200,
//!   "endpoint": "/v1/messages",
//!   "request_body_bytes": 121,
//!   "response_body_bytes": 890
//! }
//! ```

use async_trait::async_trait;
use bytes::{Bytes, BytesMut};
use lazy_static::lazy_static;
use praxis_filter::{
    BodyAccess, BodyMode, FilterAction, FilterError, HttpFilter, HttpFilterContext,
};
use serde::{Deserialize, Serialize};
use serde_yaml;
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Write};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::{debug, info, warn};

const DEFAULT_MAX_BODY_BYTES: usize = 4 * 1024 * 1024; // 4 MiB (kept for config compat)
const METRICS_FILE_PATH: &str = "/tmp/benchmark_metrics.jsonl";

/// Vertex AI `internal-*` response headers — input/output token counts.
const VERTEX_INPUT_TOKENS_HEADER: &str = "internal-input-tokens";
const VERTEX_OUTPUT_TOKENS_HEADER: &str = "internal-output-tokens";

/// Vertex AI `x-vertex-ai-*` response headers — cache token counts.
/// These are present even after `vertex_event` is stripped from the SSE stream,
/// making them the reliable fallback for cache cost calculations.
const VERTEX_CACHE_READ_HEADER: &str = "x-vertex-ai-cache-read-input-tokens";
const VERTEX_CACHE_CREATION_HEADER: &str = "x-vertex-ai-cache-creation-input-tokens";

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

/// Per-request state accumulated across `on_response_body` calls.
#[derive(Clone, Debug, Default)]
struct MetricsData {
    request_id: String,
    start_timestamp_ms: u64,
    endpoint: String,
    status_code: u16,
    request_body_bytes: usize,
    response_body_bytes: usize,
    input_tokens: u64,
    output_tokens: u64,
    cache_read_input_tokens: u64,
    cache_creation_input_tokens: u64,
    is_streaming: bool,
    /// True when we can parse the SSE body (i.e. not gzip-compressed).
    /// Vertex AI sends `Content-Encoding: gzip`; in that case the raw bytes
    /// reaching our filter are compressed and must pass through unmodified so
    /// the harness can decompress them.  Token metrics fall back to headers.
    can_parse_body: bool,
    /// Incomplete SSE event bytes carried forward from the previous chunk.
    /// Events can straddle TCP chunk boundaries; this buffer ensures we
    /// always parse complete events.
    sse_partial: Vec<u8>,
}

impl MetricsData {
    fn to_metric(&self, duration_ms: u64) -> BenchmarkMetric {
        BenchmarkMetric {
            request_id: self.request_id.clone(),
            timestamp_ms: self.start_timestamp_ms,
            input_tokens: self.input_tokens,
            output_tokens: self.output_tokens,
            cache_read_input_tokens: self.cache_read_input_tokens,
            cache_creation_input_tokens: self.cache_creation_input_tokens,
            total_tokens: self.input_tokens
                + self.output_tokens
                + self.cache_read_input_tokens
                + self.cache_creation_input_tokens,
            duration_ms,
            status_code: self.status_code,
            endpoint: self.endpoint.clone(),
            request_body_bytes: self.request_body_bytes,
            response_body_bytes: self.response_body_bytes,
        }
    }
}

/// Benchmark metrics collection filter.
pub struct BenchmarkMetricsFilter {
    #[allow(dead_code)] // retained for YAML config compatibility
    max_body_bytes: usize,
}

impl BenchmarkMetricsFilter {
    pub fn new(max_body_bytes: Option<usize>) -> Self {
        Self {
            max_body_bytes: max_body_bytes.unwrap_or(DEFAULT_MAX_BODY_BYTES),
        }
    }

    /// Construct from YAML config.
    pub fn from_config(config: &serde_yaml::Value) -> Result<Box<dyn HttpFilter>, FilterError> {
        let max_body_bytes = config
            .get("max_body_bytes")
            .and_then(|v| v.as_u64())
            .map(|v| v as usize);

        Ok(Box::new(Self::new(max_body_bytes)))
    }

    /// Extract a token count from a header value string.
    fn extract_token_count(value: &str) -> Option<u64> {
        value.trim().parse::<u64>().ok()
    }

    /// Write metric to JSONL file (thread-safe).
    fn write_metric(metric: &BenchmarkMetric) -> Result<(), FilterError> {
        let json = serde_json::to_string(metric)
            .map_err(|e| FilterError::from(format!("metrics serialize failed: {e}")))?;

        let mut file = METRICS_FILE
            .lock()
            .map_err(|e| FilterError::from(format!("metrics lock failed: {e}")))?;

        writeln!(file, "{}", json)
            .map_err(|e| FilterError::from(format!("metrics write failed: {e}")))?;

        file.flush()
            .map_err(|e| FilterError::from(format!("metrics flush failed: {e}")))?;

        debug!(request_id = metric.request_id, "wrote metric to file");
        Ok(())
    }

    /// Extract tokens from an Anthropic-style usage object.
    ///
    /// Only overwrites a field if the incoming value is non-zero OR the
    /// current value is still zero (so header-extracted values aren't
    /// clobbered by a zero in a later event).
    fn extract_anthropic_usage(usage: &serde_json::Value, data: &mut MetricsData) {
        if let Some(v) = usage.get("input_tokens").and_then(|v| v.as_u64()) {
            if v > 0 || data.input_tokens == 0 {
                data.input_tokens = v;
            }
        }
        if let Some(v) = usage.get("output_tokens").and_then(|v| v.as_u64()) {
            if v > 0 || data.output_tokens == 0 {
                data.output_tokens = v;
            }
        }
        if let Some(v) = usage.get("cache_read_input_tokens").and_then(|v| v.as_u64()) {
            if v > 0 || data.cache_read_input_tokens == 0 {
                data.cache_read_input_tokens = v;
            }
        }
        if let Some(v) = usage.get("cache_creation_input_tokens").and_then(|v| v.as_u64()) {
            if v > 0 || data.cache_creation_input_tokens == 0 {
                data.cache_creation_input_tokens = v;
            }
        }
    }

    /// Extract tokens from a JSON response body (non-streaming best-effort).
    ///
    /// With `BodyMode::Stream` the filter sees individual network chunks, not
    /// the full assembled body, so this is only reliable when the entire JSON
    /// body fits in the final chunk. Token counts from Vertex response headers
    /// (set in `on_response`) are the primary source for non-streaming; this
    /// is a fallback that fills in cache tokens if they appear in the body.
    fn extract_from_json_body(body: &[u8], data: &mut MetricsData) {
        let value: serde_json::Value = match serde_json::from_slice(body) {
            Ok(v) => v,
            Err(e) => {
                debug!(error = %e, "non-streaming body is not parseable JSON (may be partial chunk)");
                return;
            }
        };

        if let Some(usage) = value.get("usage") {
            // Try Anthropic format first (has "input_tokens")
            if usage.get("input_tokens").is_some() {
                Self::extract_anthropic_usage(usage, data);
            } else {
                // Fall back to OpenAI format ("prompt_tokens" / "completion_tokens")
                if let Some(v) = usage.get("prompt_tokens").and_then(|v| v.as_u64()) {
                    if v > 0 || data.input_tokens == 0 {
                        data.input_tokens = v;
                    }
                }
                if let Some(v) = usage.get("completion_tokens").and_then(|v| v.as_u64()) {
                    if v > 0 || data.output_tokens == 0 {
                        data.output_tokens = v;
                    }
                }
            }
        }
    }

    /// Extract token data from a single complete SSE event block.
    ///
    /// An event block is the text between two `\n\n` separators, e.g.:
    /// ```text
    /// event: message_start
    /// data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}
    /// ```
    fn extract_tokens_from_sse_block(block: &str, data: &mut MetricsData) {
        let mut event_type = "";
        let mut event_data = "";

        for line in block.lines() {
            if let Some(val) = line.strip_prefix("event:") {
                event_type = val.trim();
            } else if let Some(val) = line.strip_prefix("data:") {
                event_data = val.trim();
            }
        }

        if event_data.is_empty() || event_data == "[DONE]" {
            return;
        }

        let evt: serde_json::Value = match serde_json::from_str(event_data) {
            Ok(v) => v,
            Err(e) => {
                debug!(event_type, error = %e, "failed to parse SSE event data");
                return;
            }
        };

        match event_type {
            "vertex_event" => {
                // Vertex-specific event with authoritative usage (incl. cache tokens).
                // The Anthropic SDK silently ignores this event type.
                if let Some(usage) = evt.get("usage") {
                    Self::extract_anthropic_usage(usage, data);
                    debug!("extracted tokens from vertex_event");
                }
            }
            "message_start" => {
                if let Some(msg) = evt.get("message") {
                    if let Some(usage) = msg.get("usage") {
                        Self::extract_anthropic_usage(usage, data);
                        debug!("extracted tokens from message_start");
                    }
                }
            }
            "message_delta" => {
                // output_tokens is cumulative — always take the latest value.
                if let Some(usage) = evt.get("usage") {
                    if let Some(v) = usage.get("output_tokens").and_then(|v| v.as_u64()) {
                        data.output_tokens = v;
                        debug!(output_tokens = v, "updated output_tokens from message_delta");
                    }
                }
            }
            _ => {
                // ping, content_block_*, message_stop, etc. — no token data
                // Also handles vLLM/OpenAI SSE with no event: prefix (empty event_type)
                // For empty event_type, try to extract tokens from the data JSON itself.
                if event_type.is_empty() && !evt.is_null() {
                    // vLLM/OpenAI streaming response with usage in the data JSON
                    if let Some(usage) = evt.get("usage") {
                        // Try Anthropic format first (input_tokens / output_tokens)
                        if usage.get("input_tokens").is_some() {
                            Self::extract_anthropic_usage(usage, data);
                            debug!("extracted tokens from vLLM/OpenAI SSE (Anthropic-style format)");
                        } else {
                            // Fall back to OpenAI format (prompt_tokens / completion_tokens)
                            if let Some(v) = usage.get("prompt_tokens").and_then(|v| v.as_u64()) {
                                if v > 0 || data.input_tokens == 0 {
                                    data.input_tokens = v;
                                }
                            }
                            if let Some(v) = usage.get("completion_tokens").and_then(|v| v.as_u64()) {
                                if v > 0 || data.output_tokens == 0 {
                                    data.output_tokens = v;
                                }
                            }
                            if data.input_tokens > 0 || data.output_tokens > 0 {
                                debug!(
                                    input_tokens = data.input_tokens,
                                    output_tokens = data.output_tokens,
                                    "extracted tokens from vLLM/OpenAI SSE (prompt_tokens/completion_tokens)"
                                );
                            }
                        }
                    }
                }
            }
        }
    }

    /// Process one body chunk for an SSE stream.
    ///
    /// Combines leftover bytes from the previous call (`data.sse_partial`)
    /// with the current chunk, then splits on `\n\n` to find complete event
    /// blocks. For each complete block:
    ///
    /// - `vertex_event`: extract tokens, **strip** from output (Vercel AI SDK
    ///   crashes on unknown discriminated union members).
    /// - anything else: extract tokens, pass through to output.
    ///
    /// The trailing incomplete block is saved to `data.sse_partial` for the
    /// next call and is **not** sent yet — this ensures partial `vertex_event`
    /// bytes never reach the harness.
    ///
    /// Returns the filtered bytes to replace the body chunk with.
    fn process_sse_chunk(chunk: &[u8], data: &mut MetricsData, end_of_stream: bool) -> Bytes {
        // Combine partial leftovers with current chunk
        let mut combined = std::mem::take(&mut data.sse_partial);
        combined.extend_from_slice(chunk);

        let text = match std::str::from_utf8(&combined) {
            Ok(s) => s.to_owned(),
            Err(_) => {
                // Chunk boundary split a multi-byte UTF-8 sequence — save for next call.
                // Caller uses None (not Some(empty)) so the stream stays open.
                warn!("SSE chunk contains invalid UTF-8, deferring");
                data.sse_partial = combined;
                return Bytes::new();  // caller converts empty → None
            }
        };

        // Split on double-newline SSE event separator.
        // All elements except the last are complete event blocks.
        let blocks: Vec<&str> = text.split("\n\n").collect();
        let n = blocks.len();

        let (complete_count, save_last) = if end_of_stream {
            (n, false)
        } else {
            // Last block may be an incomplete event — hold it
            (n.saturating_sub(1), true)
        };

        let mut output = BytesMut::new();

        for block in &blocks[..complete_count] {
            if block.trim().is_empty() {
                continue;
            }
            // Peek at the event type before extracting tokens
            let is_vertex_event = block
                .lines()
                .any(|l| l.strip_prefix("event:").map(|v| v.trim() == "vertex_event").unwrap_or(false));

            Self::extract_tokens_from_sse_block(block, data);

            if !is_vertex_event {
                output.extend_from_slice(block.as_bytes());
                output.extend_from_slice(b"\n\n");
            } else {
                debug!("stripped vertex_event from SSE output");
            }
        }

        if save_last {
            let last = blocks.last().copied().unwrap_or("");
            if !last.is_empty() {
                data.sse_partial = last.as_bytes().to_vec();
            }
        }

        output.freeze()
    }
}

#[async_trait]
impl HttpFilter for BenchmarkMetricsFilter {
    fn name(&self) -> &'static str {
        "benchmark_metrics"
    }

    fn request_body_access(&self) -> BodyAccess {
        BodyAccess::ReadOnly
    }

    fn request_body_mode(&self) -> BodyMode {
        BodyMode::Stream
    }

    /// ReadWrite: `vertex_event` SSE events are stripped from the outgoing body.
    /// All other chunks pass through unmodified.
    fn response_body_access(&self) -> BodyAccess {
        BodyAccess::ReadWrite
    }

    /// Stream mode (the default): chunks flow through to the harness
    /// immediately as they arrive. No buffering, no delivery delay.
    fn response_body_mode(&self) -> BodyMode {
        BodyMode::Stream
    }

    async fn on_request(
        &self,
        ctx: &mut HttpFilterContext<'_>,
    ) -> Result<FilterAction, FilterError> {
        // Capture request start time and endpoint here so duration_ms includes
        // the full round-trip (request → upstream → response body complete).
        let mut data = MetricsData::default();
        data.request_id = "unknown".to_string();
        data.start_timestamp_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;
        data.endpoint = ctx.request.uri.path().to_string();
        ctx.extensions.insert(data);
        Ok(FilterAction::Continue)
    }

    async fn on_request_body(
        &self,
        ctx: &mut HttpFilterContext<'_>,
        body: &mut Option<Bytes>,
        _end_of_stream: bool,
    ) -> Result<FilterAction, FilterError> {
        if let Some(mut data) = ctx.extensions.get::<MetricsData>().cloned() {
            if let Some(chunk) = body.as_ref() {
                data.request_body_bytes += chunk.len();
                ctx.extensions.insert(data);
            }
        }
        Ok(FilterAction::Continue)
    }

    async fn on_response(
        &self,
        ctx: &mut HttpFilterContext<'_>,
    ) -> Result<FilterAction, FilterError> {
        let response_headers = match ctx.response_header.as_ref() {
            Some(resp) => &resp.headers,
            None => {
                debug!("on_response: no response headers");
                return Ok(FilterAction::Continue);
            }
        };

        // Retrieve data seeded in on_request; fall back to a fresh record if
        // on_request was skipped (e.g. filter conditions excluded it).
        let mut data = ctx
            .extensions
            .get::<MetricsData>()
            .cloned()
            .unwrap_or_else(|| {
                let mut d = MetricsData::default();
                d.request_id = "unknown".to_string();
                d.start_timestamp_ms = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_millis() as u64;
                d
            });

        data.endpoint = if data.endpoint.is_empty() {
            ctx.request.uri.path().to_string()
        } else {
            data.endpoint.clone()
        };

        data.status_code = ctx
            .response_header
            .as_ref()
            .map(|resp| resp.status.as_u16())
            .unwrap_or(200);

        let content_type = response_headers
            .get(http::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        data.is_streaming = content_type.contains("text/event-stream");

        // If the response is compressed (Vertex AI sends gzip), the raw bytes
        // reaching on_response_body are not parseable as UTF-8 SSE text.
        // Set can_parse_body = false so we pass bytes through unmodified and
        // rely solely on response headers for token counts.
        let content_encoding = response_headers
            .get(http::header::CONTENT_ENCODING)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        data.can_parse_body = content_encoding.is_empty()
            || content_encoding.eq_ignore_ascii_case("identity");

        debug!(
            is_streaming = data.is_streaming,
            can_parse_body = data.can_parse_body,
            content_type = content_type,
            content_encoding = content_encoding,
            status_code = data.status_code,
            "response received"
        );

        // Extract token counts from Vertex response headers.
        // These are the primary source for non-streaming and a useful
        // fallback for streaming when SSE events are absent/malformed.
        if let Some(val) = response_headers.get(VERTEX_INPUT_TOKENS_HEADER) {
            if let Ok(s) = val.to_str() {
                if let Some(count) = Self::extract_token_count(s) {
                    data.input_tokens = count;
                    debug!(input_tokens = count, "extracted from Vertex header");
                }
            }
        }

        if let Some(val) = response_headers.get(VERTEX_OUTPUT_TOKENS_HEADER) {
            if let Ok(s) = val.to_str() {
                if let Some(count) = Self::extract_token_count(s) {
                    data.output_tokens = count;
                    debug!(output_tokens = count, "extracted from Vertex header");
                }
            }
        }

        // Cache token headers — present even after vertex_event is stripped.
        // Primary source for cache cost calculations.
        if let Some(val) = response_headers.get(VERTEX_CACHE_READ_HEADER) {
            if let Ok(s) = val.to_str() {
                if let Some(count) = Self::extract_token_count(s) {
                    data.cache_read_input_tokens = count;
                    debug!(cache_read_input_tokens = count, "extracted from Vertex header");
                }
            }
        }

        if let Some(val) = response_headers.get(VERTEX_CACHE_CREATION_HEADER) {
            if let Ok(s) = val.to_str() {
                if let Some(count) = Self::extract_token_count(s) {
                    data.cache_creation_input_tokens = count;
                    debug!(cache_creation_input_tokens = count, "extracted from Vertex header");
                }
            }
        }

        ctx.extensions.insert(data);
        Ok(FilterAction::Continue)
    }

    fn on_response_body(
        &self,
        ctx: &mut HttpFilterContext<'_>,
        body: &mut Option<Bytes>,
        end_of_stream: bool,
    ) -> Result<FilterAction, FilterError> {
        let mut data = match ctx.extensions.get::<MetricsData>() {
            Some(d) => d.clone(),
            None => {
                debug!("no metrics data in extensions, skipping");
                return Ok(FilterAction::Continue);
            }
        };

        // Process the chunk: strip vertex_event from SSE, extract token metrics.
        if let Some(chunk) = body.as_ref() {
            data.response_body_bytes += chunk.len();

            if data.can_parse_body {
                if data.is_streaming {
                    let filtered = Self::process_sse_chunk(chunk, &mut data, end_of_stream);
                    // Use None (not Some(empty)) when there is no output for this
                    // chunk.  In HTTP/1.1 chunked encoding, Some(Bytes::new()) is
                    // the 0-length terminator chunk and would close the stream
                    // prematurely — causing pi's "stream ended without stop reason"
                    // and opencode's loop when the partial buffer holds bytes
                    // mid-event or when vertex_event is the only event in a chunk.
                    *body = if filtered.is_empty() { None } else { Some(filtered) };
                } else if end_of_stream {
                    // Non-streaming: attempt JSON parse on the final chunk.
                    // Vertex headers (set in on_response) are the primary token source;
                    // this fills in cache tokens if they appear in the body.
                    Self::extract_from_json_body(chunk, &mut data);
                }
            }
            // If !can_parse_body (e.g. Content-Encoding: gzip), leave body
            // unmodified so the harness can decompress it itself.  Token
            // metrics come from the Vertex response headers set in on_response.
        }

        // Persist updated state for the next chunk call
        ctx.extensions.insert(data.clone());

        if end_of_stream {
            let now_ms = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64;
            let duration_ms = now_ms.saturating_sub(data.start_timestamp_ms);
            let metric = data.to_metric(duration_ms);

            info!(
                input_tokens = metric.input_tokens,
                output_tokens = metric.output_tokens,
                cache_read = metric.cache_read_input_tokens,
                cache_creation = metric.cache_creation_input_tokens,
                total_tokens = metric.total_tokens,
                duration_ms = metric.duration_ms,
                response_bytes = metric.response_body_bytes,
                "writing benchmark metric"
            );

            Self::write_metric(&metric)?;
        }

        Ok(FilterAction::Continue)
    }
}
