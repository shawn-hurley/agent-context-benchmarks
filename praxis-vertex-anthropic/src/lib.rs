//! Praxis filters for Vertex AI Anthropic integration.
//!
//! ## `vertex_anthropic_prepare`
//!
//! Rewrites Anthropic Messages requests to comply with the Vertex AI
//! Anthropic endpoint wire format, which differs from the direct Anthropic
//! API in two ways:
//!
//! 1. The `model` field must NOT appear in the request body — the model is
//!    encoded in the URL path by the upstream `path_rewrite` filter.
//!    Vertex returns HTTP 400 `"model: Extra inputs are not permitted"` if
//!    the field is present.
//!
//! 2. `anthropic_version` must appear in the request body (not as a header)
//!    with the value `"vertex-2023-10-16"`.  The standard Anthropic SDK
//!    sends it as an `anthropic-version` header; Vertex returns HTTP 400
//!    `"anthropic_version: Field required"` if the body field is absent.
//!
//! This filter handles both rewrites in a single `on_request_body` pass and
//! removes the now-redundant `anthropic-version` header in `on_request`.
//!
//! ## `benchmark_metrics`
//!
//! Collects token usage metrics and strips `vertex_event` SSE events.
//! See `metrics_collector.rs` for full documentation.
//!
//! ## `token_usage_to_metrics`
//!
//! Reads token usage from filter_metadata (written by the `token_count` filter)
//! and writes benchmark metrics to JSONL file with request_id deduplication.
//! See `token_usage_to_metrics.rs` for full documentation.

mod metrics_collector;
mod token_usage_to_metrics;

use async_trait::async_trait;
use bytes::Bytes;
use praxis_filter::{
    BodyAccess, BodyMode, FilterAction, FilterError, FilterRegistry, HttpFilter, HttpFilterContext,
};

use metrics_collector::BenchmarkMetricsFilter;
use token_usage_to_metrics::TokenUsageToMetricsFilter;

const DEFAULT_MAX_BODY_BYTES: usize = 4 * 1024 * 1024; // 4 MiB
const ANTHROPIC_VERSION_FIELD: &str = "anthropic_version";
const ANTHROPIC_VERSION_VALUE: &str = "vertex-2023-10-16";
const MODEL_FIELD: &str = "model";
const ANTHROPIC_VERSION_HEADER: &str = "anthropic-version";

/// Rewrites an Anthropic Messages request body for the Vertex AI endpoint:
/// removes the `model` field and injects `anthropic_version` as a body field.
pub struct VertexAnthropicPrepareFilter {
    max_body_bytes: usize,
}

impl VertexAnthropicPrepareFilter {
    /// Construct from YAML config.
    ///
    /// # Errors
    ///
    /// Returns [`FilterError`] if `max_body_bytes` is present but not a
    /// valid unsigned integer.
    pub fn from_config(config: &serde_yaml::Value) -> Result<Box<dyn HttpFilter>, FilterError> {
        let max_body_bytes = config
            .get("max_body_bytes")
            .and_then(serde_yaml::Value::as_u64)
            .map(|v| v as usize)
            .unwrap_or(DEFAULT_MAX_BODY_BYTES);

        Ok(Box::new(Self { max_body_bytes }))
    }
}

#[async_trait]
impl HttpFilter for VertexAnthropicPrepareFilter {
    fn name(&self) -> &'static str {
        "vertex_anthropic_prepare"
    }

    fn request_body_access(&self) -> BodyAccess {
        BodyAccess::ReadWrite
    }

    fn request_body_mode(&self) -> BodyMode {
        BodyMode::StreamBuffer {
            max_bytes: Some(self.max_body_bytes),
        }
    }

    async fn on_request(
        &self,
        ctx: &mut HttpFilterContext<'_>,
    ) -> Result<FilterAction, FilterError> {
        // Remove the anthropic-version header that the Anthropic SDK sends.
        // Vertex does not accept it as a header; the body field is required.
        ctx.request_headers_to_remove
            .push(http::header::HeaderName::from_static(
                ANTHROPIC_VERSION_HEADER,
            ));
        Ok(FilterAction::Continue)
    }

    async fn on_request_body(
        &self,
        _ctx: &mut HttpFilterContext<'_>,
        body: &mut Option<Bytes>,
        end_of_stream: bool,
    ) -> Result<FilterAction, FilterError> {
        // StreamBuffer delivers the complete body on the final chunk.
        if !end_of_stream {
            return Ok(FilterAction::Continue);
        }

        let bytes = match body.as_ref() {
            Some(b) if !b.is_empty() => b,
            _ => return Ok(FilterAction::Continue),
        };

        let mut value: serde_json::Value = match serde_json::from_slice(bytes) {
            Ok(v) => v,
            // Not JSON (e.g. a GET /v1/models) — leave untouched.
            Err(_) => return Ok(FilterAction::Continue),
        };

        let obj = match value.as_object_mut() {
            Some(o) => o,
            None => return Ok(FilterAction::Continue),
        };

        // 1. Remove `model` — Vertex routes by URL path, not body field.
        obj.remove(MODEL_FIELD);

        // 2. Inject `anthropic_version` with the Vertex-required value.
        //    Overwrite if already present (e.g. a caller that set it wrong).
        obj.insert(
            ANTHROPIC_VERSION_FIELD.to_string(),
            serde_json::Value::String(ANTHROPIC_VERSION_VALUE.to_string()),
        );

        *body = Some(Bytes::from(serde_json::to_vec(&value).map_err(|e| {
            FilterError::from(format!("vertex_anthropic_prepare: serialize failed: {e}"))
        })?));

        Ok(FilterAction::Continue)
    }
}

/// Register this crate's filters into `registry`.
///
/// Called automatically by `praxis-ai-proxy`'s build-script-generated
/// `register_external_filters()` function, which discovers this crate via
/// the `[package.metadata.praxis-filters]` marker in `Cargo.toml`.
pub fn register_filters(registry: &mut FilterRegistry) {
    praxis_filter::register_filters!(
        @register registry,
        http "vertex_anthropic_prepare" => VertexAnthropicPrepareFilter::from_config
    );
    praxis_filter::register_filters!(
        @register registry,
        http "benchmark_metrics" => BenchmarkMetricsFilter::from_config
    );
    praxis_filter::register_filters!(
        @register registry,
        http "token_usage_to_metrics" => TokenUsageToMetricsFilter::from_config
    );
}
