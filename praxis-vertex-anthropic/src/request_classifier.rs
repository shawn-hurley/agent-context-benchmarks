//! Praxis filter: `request_classifier`
//!
//! Classifies LLM request content types and stores classification in ctx.extensions.
//! Uses BodyMode::StreamBuffer so Praxis automatically delivers the complete request body.

use async_trait::async_trait;
use bytes::Bytes;
use praxis_filter::{
    BodyAccess, BodyMode, FilterAction, FilterError, HttpFilter, HttpFilterContext,
};
use crate::token_usage_to_metrics::{ContentType, RequestClassification};

const DEFAULT_MAX_BODY_BYTES: usize = 4 * 1024 * 1024; // 4 MiB

pub struct RequestClassifierFilter {
    max_body_bytes: usize,
}

impl RequestClassifierFilter {
    pub fn from_config(config: &serde_yaml::Value) -> Result<Box<dyn HttpFilter>, FilterError> {
        let max_body_bytes = config
            .get("max_body_bytes")
            .and_then(|v| v.as_u64())
            .map(|v| v as usize)
            .unwrap_or(DEFAULT_MAX_BODY_BYTES);
        Ok(Box::new(Self { max_body_bytes }))
    }

    fn classify_request(body: &Bytes, endpoint: &str) -> Option<RequestClassification> {
        let json: serde_json::Value = serde_json::from_slice(body).ok()?;

        let is_openai = endpoint.contains("/v1/chat/completions");
        let is_anthropic = endpoint.contains("/v1/messages");

        if !is_openai && !is_anthropic {
            return None;
        }

        let messages = json.get("messages").and_then(|m| m.as_array());
        let tools = json.get("tools").and_then(|t| t.as_array());
        let system = json.get("system");

        let message_count = messages.map(|m| m.len() as u32);

        let (content_type, tool_name, tool_detail, tools_used) = if let Some(msgs) = messages {
            if msgs.is_empty() {
                (ContentType::Unknown, None, None, 0)
            } else {
                if is_openai {
                    Self::classify_openai_request(msgs, system.is_some())
                } else {
                    Self::classify_anthropic_request(msgs, system.is_some())
                }
            }
        } else if system.is_some() {
            (ContentType::SystemPrompt, None, None, 0)
        } else {
            (ContentType::Unknown, None, None, 0)
        };

        // Use tools_used if we found tools in this turn, otherwise count available tools
        let tool_count = if tools_used > 0 {
            Some(tools_used)
        } else {
            tools.map(|t| t.len() as u32)
        };

        Some(RequestClassification {
            content_type,
            tool_name,
            tool_detail,
            tool_count,
            message_count,
        })
    }

    fn classify_openai_request(
        messages: &[serde_json::Value],
        has_system: bool,
    ) -> (ContentType, Option<String>, Option<String>, u32) {
        // Turn 1: Initial system prompt + user message
        if messages.len() == 1 && has_system {
            return (ContentType::SystemPrompt, None, None, 0);
        }

        // Find last USER message (OpenAI uses "user" role for tool results)
        let last_user = messages.iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("user"));

        if let Some(user_msg) = last_user {
            if let Some(content) = user_msg.get("content") {
                // In OpenAI format, tool results are typically in the content as objects
                // For now, check if content indicates tool results
                let has_tool_result = if let serde_json::Value::Array(arr) = content {
                    arr.iter().any(|c| c.get("type").and_then(|t| t.as_str()) == Some("tool_result"))
                } else if let Some(text) = content.as_str() {
                    // Simple heuristic: if content is a string, it's likely a tool result if it came after assistant
                    !text.trim().is_empty()
                } else {
                    false
                };

                if has_tool_result {
                    // Count tool results if possible
                    let tool_count = if let serde_json::Value::Array(arr) = content {
                        arr.iter().filter(|c| c.get("type").and_then(|t| t.as_str()) == Some("tool_result")).count() as u32
                    } else {
                        1
                    };
                    return (ContentType::ToolResult, None, None, tool_count);
                }
            }
        }

        // Find last ASSISTANT message
        let last_assistant = messages.iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("assistant"));

        if let Some(asst_msg) = last_assistant {
            // Check for tool_calls (OpenAI format)
            if let Some(tool_calls) = asst_msg.get("tool_calls").and_then(|t| t.as_array()) {
                if !tool_calls.is_empty() {
                    let first_call = &tool_calls[0];
                    let tool_name = first_call
                        .get("function")
                        .and_then(|f| f.get("name"))
                        .and_then(|n| n.as_str())
                        .map(|s| s.to_string());

                    let tool_detail = if let Some(ref name) = tool_name {
                        if name == "task" || name == "skill" {
                            Self::extract_tool_detail(first_call, name)
                        } else {
                            None
                        }
                    } else {
                        None
                    };

                    return (ContentType::ToolCall, tool_name, tool_detail, tool_calls.len() as u32);
                }
            }

            // Check for text content
            if let Some(_content) = asst_msg.get("content") {
                return (ContentType::AssistantContinuation, None, None, 0);
            }
        }

        // Find TOOL message (tool responses in OpenAI format)
        let last_tool = messages.iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("tool"));

        if let Some(tool_msg) = last_tool {
            let tool_name = tool_msg
                .get("name")
                .and_then(|n| n.as_str())
                .map(|s| s.to_string());
            return (ContentType::ToolResult, tool_name, None, 1);
        }

        // Default: regular user message
        (ContentType::UserMessage, None, None, 0)
    }

    fn classify_anthropic_request(
        messages: &[serde_json::Value],
        has_system: bool,
    ) -> (ContentType, Option<String>, Option<String>, u32) {
        // Turn 1: Initial system prompt + user message
        if messages.len() == 1 && has_system {
            return (ContentType::SystemPrompt, None, None, 0);
        }

        // Find last USER message
        let last_user = messages.iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("user"));

        if let Some(user_msg) = last_user {
            if let Some(content) = user_msg.get("content").and_then(|c| c.as_array()) {
                // Count tool_result blocks
                let tool_results: Vec<_> = content.iter()
                    .filter(|b| b.get("type").and_then(|t| t.as_str()) == Some("tool_result"))
                    .collect();

                // Check for text content
                let has_text = content.iter()
                    .any(|b| {
                        if let Some("text") = b.get("type").and_then(|t| t.as_str()) {
                            if let Some(text) = b.get("text").and_then(|t| t.as_str()) {
                                return !text.trim().is_empty();
                            }
                        }
                        false
                    });

                // Multiple tool results + user text → UserMessage
                if !tool_results.is_empty() && has_text {
                    return (ContentType::UserMessage, None, None, tool_results.len() as u32);
                }

                // Pure tool result(s)
                if !tool_results.is_empty() {
                    let first_tool_id = tool_results[0]
                        .get("tool_use_id")
                        .and_then(|id| id.as_str())
                        .map(|s| s.to_string());
                    return (ContentType::ToolResult, first_tool_id, None, tool_results.len() as u32);
                }
            }
        }

        // Find last ASSISTANT message
        let last_assistant = messages.iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("assistant"));

        if let Some(asst_msg) = last_assistant {
            if let Some(content) = asst_msg.get("content").and_then(|c| c.as_array()) {
                // Count tool_use blocks
                let tool_uses: Vec<_> = content.iter()
                    .filter(|b| b.get("type").and_then(|t| t.as_str()) == Some("tool_use"))
                    .collect();

                if !tool_uses.is_empty() {
                    let first_tool = &tool_uses[0];
                    let tool_name = first_tool
                        .get("name")
                        .and_then(|n| n.as_str())
                        .map(|s| s.to_string());

                    let tool_detail = if let Some(ref name) = tool_name {
                        if name == "task" || name == "skill" {
                            Self::extract_tool_detail(first_tool, name)
                        } else {
                            None
                        }
                    } else {
                        None
                    };

                    return (ContentType::ToolCall, tool_name, tool_detail, tool_uses.len() as u32);
                }

                // Assistant has content but no tool_use
                if !content.is_empty() {
                    return (ContentType::AssistantContinuation, None, None, 0);
                }
            }
        }

        // Default: regular user message
        (ContentType::UserMessage, None, None, 0)
    }

    fn extract_tool_detail(tool_block: &serde_json::Value, tool_name: &str) -> Option<String> {
        let input = tool_block.get("input").or_else(|| tool_block.get("arguments"))?;

        if tool_name == "task" {
            input.get("subagent_type").and_then(|v| v.as_str()).map(|s| s.to_string())
        } else if tool_name == "skill" {
            input.get("name").and_then(|v| v.as_str()).map(|s| s.to_string())
        } else {
            None
        }
    }
}

#[async_trait]
impl HttpFilter for RequestClassifierFilter {
    fn name(&self) -> &'static str {
        "request_classifier"
    }

    fn request_body_access(&self) -> BodyAccess {
        BodyAccess::ReadOnly
    }

    fn request_body_mode(&self) -> BodyMode {
        BodyMode::StreamBuffer {
            max_bytes: Some(self.max_body_bytes),
        }
    }

    async fn on_request(
        &self,
        _ctx: &mut HttpFilterContext<'_>,
    ) -> Result<FilterAction, FilterError> {
        Ok(FilterAction::Continue)
    }

    async fn on_request_body(
        &self,
        ctx: &mut HttpFilterContext<'_>,
        body: &mut Option<Bytes>,
        end_of_stream: bool,
    ) -> Result<FilterAction, FilterError> {
        if !end_of_stream {
            return Ok(FilterAction::Continue);
        }

        let complete_body = match body.as_ref() {
            Some(b) if !b.is_empty() => b,
            _ => return Ok(FilterAction::Continue),
        };

        let endpoint = ctx.request.uri.path().to_string();
        
        if let Some(classification) = Self::classify_request(complete_body, &endpoint) {
            ctx.extensions.insert(classification);
        }

        Ok(FilterAction::Continue)
    }
}
