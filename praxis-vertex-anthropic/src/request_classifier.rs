//! Praxis filter: `request_classifier`
//!
//! Classifies LLM request content types and stores classification in ctx.extensions.
//! Uses BodyMode::StreamBuffer so Praxis automatically delivers the complete request body.

use crate::token_usage_to_metrics::{ContentType, RequestClassification, ToolIdentity};
use async_trait::async_trait;
use bytes::Bytes;
use praxis_filter::{
    BodyAccess, BodyMode, FilterAction, FilterError, HttpFilter, HttpFilterContext,
};

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
        let available_tools = json.get("tools").and_then(|t| t.as_array());
        let system = json.get("system");

        let message_count = messages.map(|m| m.len() as u32);

        let (content_type, tool_name, tool_detail, tools, tools_used) = if let Some(msgs) = messages
        {
            if msgs.is_empty() {
                (ContentType::Unknown, None, None, Vec::new(), 0)
            } else {
                if is_openai {
                    Self::classify_openai_request(msgs, system.is_some())
                } else {
                    Self::classify_anthropic_request(msgs, system.is_some())
                }
            }
        } else if system.is_some() {
            (ContentType::SystemPrompt, None, None, Vec::new(), 0)
        } else {
            (ContentType::Unknown, None, None, Vec::new(), 0)
        };

        // Use tools_used if we found tools in this turn, otherwise count available tools
        let tool_count = if tools_used > 0 {
            Some(tools_used)
        } else {
            available_tools.map(|t| t.len() as u32)
        };

        Some(RequestClassification {
            content_type,
            tool_name,
            tool_detail,
            tools,
            tool_count,
            message_count,
        })
    }

    fn classify_openai_request(
        messages: &[serde_json::Value],
        has_system: bool,
    ) -> (
        ContentType,
        Option<String>,
        Option<String>,
        Vec<ToolIdentity>,
        u32,
    ) {
        // Turn 1: Initial system prompt + user message
        if messages.len() == 1 && has_system {
            return (ContentType::SystemPrompt, None, None, Vec::new(), 0);
        }

        // Explicit tool messages are authoritative and must be checked before
        // generic user-content heuristics.
        if let Some(tool_msg) = messages
            .iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("tool"))
        {
            let call_id = tool_msg
                .get("tool_call_id")
                .and_then(|v| v.as_str())
                .map(str::to_string);
            let identity =
                Self::find_openai_tool_identity(messages, call_id.as_deref()).or_else(|| {
                    tool_msg
                        .get("name")
                        .and_then(|v| v.as_str())
                        .map(|name| ToolIdentity {
                            name: name.to_string(),
                            detail: None,
                            call_id: call_id.clone(),
                        })
                });
            if let Some(tool) = identity {
                let name = tool.name.clone();
                return (ContentType::ToolResult, Some(name), None, vec![tool], 1);
            }
            return (ContentType::ToolResult, None, None, Vec::new(), 1);
        }

        // Find last USER message (OpenAI uses "user" role for tool results)
        let last_user = messages
            .iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("user"));

        if let Some(user_msg) = last_user {
            if let Some(content) = user_msg.get("content") {
                // In OpenAI format, tool results are typically in the content as objects
                // For now, check if content indicates tool results
                let has_tool_result = if let serde_json::Value::Array(arr) = content {
                    arr.iter()
                        .any(|c| c.get("type").and_then(|t| t.as_str()) == Some("tool_result"))
                } else if let Some(text) = content.as_str() {
                    // Simple heuristic: if content is a string, it's likely a tool result if it came after assistant
                    !text.trim().is_empty()
                } else {
                    false
                };

                if has_tool_result {
                    // Count tool results if possible
                    let tool_count = if let serde_json::Value::Array(arr) = content {
                        arr.iter()
                            .filter(|c| {
                                c.get("type").and_then(|t| t.as_str()) == Some("tool_result")
                            })
                            .count() as u32
                    } else {
                        1
                    };
                    return (ContentType::ToolResult, None, None, Vec::new(), tool_count);
                }
            }
        }

        // Find last ASSISTANT message
        let last_assistant = messages
            .iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("assistant"));

        if let Some(asst_msg) = last_assistant {
            // Check for tool_calls (OpenAI format)
            if let Some(tool_calls) = asst_msg.get("tool_calls").and_then(|t| t.as_array()) {
                if !tool_calls.is_empty() {
                    let tools = tool_calls
                        .iter()
                        .filter_map(Self::openai_tool_identity)
                        .collect::<Vec<_>>();
                    let first = tools.first();
                    return (
                        ContentType::ToolCall,
                        first.map(|t| t.name.clone()),
                        first.and_then(|t| t.detail.clone()),
                        tools,
                        tool_calls.len() as u32,
                    );
                }
            }

            // Check for text content
            if let Some(_content) = asst_msg.get("content") {
                return (
                    ContentType::AssistantContinuation,
                    None,
                    None,
                    Vec::new(),
                    0,
                );
            }
        }

        // Default: regular user message
        (ContentType::UserMessage, None, None, Vec::new(), 0)
    }

    fn classify_anthropic_request(
        messages: &[serde_json::Value],
        has_system: bool,
    ) -> (
        ContentType,
        Option<String>,
        Option<String>,
        Vec<ToolIdentity>,
        u32,
    ) {
        // Turn 1: Initial system prompt + user message
        if messages.len() == 1 && has_system {
            return (ContentType::SystemPrompt, None, None, Vec::new(), 0);
        }

        let tool_by_id = Self::anthropic_tool_map(messages);

        // Find last USER message
        let last_user = messages
            .iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("user"));

        if let Some(user_msg) = last_user {
            if let Some(content) = user_msg.get("content").and_then(|c| c.as_array()) {
                // Count tool_result blocks
                let tool_results: Vec<_> = content
                    .iter()
                    .filter(|b| b.get("type").and_then(|t| t.as_str()) == Some("tool_result"))
                    .collect();

                // Check for text content
                let has_text = content.iter().any(|b| {
                    if let Some("text") = b.get("type").and_then(|t| t.as_str()) {
                        if let Some(text) = b.get("text").and_then(|t| t.as_str()) {
                            return !text.trim().is_empty();
                        }
                    }
                    false
                });

                // Multiple tool results + user text → UserMessage
                if !tool_results.is_empty() && has_text {
                    return (
                        ContentType::UserMessage,
                        None,
                        None,
                        Vec::new(),
                        tool_results.len() as u32,
                    );
                }

                // Pure tool result(s)
                if !tool_results.is_empty() {
                    let first_tool_id = tool_results[0]
                        .get("tool_use_id")
                        .and_then(|id| id.as_str())
                        .map(|s| s.to_string());
                    let tools = tool_results
                        .iter()
                        .filter_map(|result| {
                            let id = result.get("tool_use_id").and_then(|v| v.as_str())?;
                            tool_by_id.get(id).cloned().or_else(|| {
                                Some(ToolIdentity {
                                    name: "unknown".to_string(),
                                    detail: None,
                                    call_id: Some(id.to_string()),
                                })
                            })
                        })
                        .collect::<Vec<_>>();
                    let first_name = tools.first().map(|t| t.name.clone()).or(first_tool_id);
                    return (
                        ContentType::ToolResult,
                        first_name,
                        None,
                        tools,
                        tool_results.len() as u32,
                    );
                }
            }
        }

        // Find last ASSISTANT message
        let last_assistant = messages
            .iter()
            .rev()
            .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("assistant"));

        if let Some(asst_msg) = last_assistant {
            if let Some(content) = asst_msg.get("content").and_then(|c| c.as_array()) {
                // Count tool_use blocks
                let tool_uses: Vec<_> = content
                    .iter()
                    .filter(|b| b.get("type").and_then(|t| t.as_str()) == Some("tool_use"))
                    .collect();

                if !tool_uses.is_empty() {
                    let tool_count = tool_uses.len() as u32;
                    let tools = tool_uses
                        .into_iter()
                        .filter_map(Self::anthropic_tool_identity)
                        .collect::<Vec<_>>();
                    let first = tools.first();
                    return (
                        ContentType::ToolCall,
                        first.map(|t| t.name.clone()),
                        first.and_then(|t| t.detail.clone()),
                        tools,
                        tool_count,
                    );
                }

                // Assistant has content but no tool_use
                if !content.is_empty() {
                    return (
                        ContentType::AssistantContinuation,
                        None,
                        None,
                        Vec::new(),
                        0,
                    );
                }
            }
        }

        // Default: regular user message
        (ContentType::UserMessage, None, None, Vec::new(), 0)
    }

    fn openai_tool_identity(call: &serde_json::Value) -> Option<ToolIdentity> {
        let function = call.get("function")?;
        let name = function.get("name")?.as_str()?.to_string();
        let detail = Self::extract_tool_detail(call, &name);
        let call_id = call.get("id").and_then(|v| v.as_str()).map(str::to_string);
        Some(ToolIdentity {
            name,
            detail,
            call_id,
        })
    }

    fn find_openai_tool_identity(
        messages: &[serde_json::Value],
        call_id: Option<&str>,
    ) -> Option<ToolIdentity> {
        let call_id = call_id?;
        messages.iter().rev().find_map(|message| {
            let calls = message.get("tool_calls")?.as_array()?;
            calls.iter().find_map(|call| {
                if call.get("id").and_then(|v| v.as_str()) == Some(call_id) {
                    let identity = Self::openai_tool_identity(call)?;
                    Some(identity)
                } else {
                    None
                }
            })
        })
    }

    fn anthropic_tool_identity(tool: &serde_json::Value) -> Option<ToolIdentity> {
        let name = tool.get("name")?.as_str()?.to_string();
        let detail = Self::extract_tool_detail(tool, &name);
        let call_id = tool.get("id").and_then(|v| v.as_str()).map(str::to_string);
        Some(ToolIdentity {
            name,
            detail,
            call_id,
        })
    }

    fn anthropic_tool_map(
        messages: &[serde_json::Value],
    ) -> std::collections::HashMap<String, ToolIdentity> {
        messages
            .iter()
            .flat_map(|message| {
                message
                    .get("content")
                    .and_then(|content| content.as_array())
                    .into_iter()
                    .flatten()
                    .filter(|block| block.get("type").and_then(|v| v.as_str()) == Some("tool_use"))
                    .filter_map(Self::anthropic_tool_identity)
                    .filter_map(|tool| tool.call_id.clone().map(|id| (id, tool)))
            })
            .collect()
    }

    fn extract_primary_command(command: &str) -> Option<String> {
        // Navigation commands to skip
        const SKIP_COMMANDS: &[&str] = &["cd", "pushd", "popd"];

        // Split on shell operators: &&, ||, ;, |
        // We'll do a simple character-based split for simplicity
        let parts: Vec<&str> = command
            .split(&['&', '|', ';'][..])
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
            .collect();

        // Find first non-navigation command
        for part in parts {
            // Extract first word (command name)
            if let Some(first_word) = part.split_whitespace().next() {
                // Skip navigation commands
                if !SKIP_COMMANDS.contains(&first_word) {
                    return Some(first_word.to_string());
                }
            }
        }

        // If all commands were navigation commands, return the first one
        // Example: "cd /tmp" -> return "cd"
        command.split_whitespace().next().map(|s| s.to_string())
    }

    fn extract_tool_detail(tool_block: &serde_json::Value, tool_name: &str) -> Option<String> {
        let input = tool_block
            .get("input")
            .or_else(|| tool_block.get("arguments"))
            .or_else(|| tool_block.get("function").and_then(|f| f.get("arguments")))?;
        let input_storage;
        let input = if let Some(arguments) = input.as_str() {
            input_storage = serde_json::from_str(arguments).ok()?;
            &input_storage
        } else {
            input
        };

        if tool_name == "task" {
            input
                .get("subagent_type")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        } else if tool_name == "skill" {
            input
                .get("name")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        } else if tool_name == "bash" || tool_name == "shell" {
            // Extract primary command from bash tool call
            input
                .get("command")
                .and_then(|v| v.as_str())
                .and_then(|cmd| Self::extract_primary_command(cmd))
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

#[cfg(test)]
mod tests {
    use super::RequestClassifierFilter;
    use crate::token_usage_to_metrics::ContentType;
    use bytes::Bytes;
    use serde_json::json;

    #[test]
    fn resolves_anthropic_tool_result_to_tool_name() {
        let body = json!({
            "messages": [
                {"role": "assistant", "content": [{
                    "type": "tool_use", "id": "toolu_1", "name": "bash",
                    "input": {"command": "cd /tmp && rg TODO"}
                }]},
                {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "toolu_1", "content": "matches"
                }]}
            ]
        });
        let classification = RequestClassifierFilter::classify_request(
            &Bytes::from(serde_json::to_vec(&body).unwrap()),
            "/v1/messages",
        )
        .unwrap();
        assert_eq!(classification.content_type, ContentType::ToolResult);
        assert_eq!(classification.tools[0].name, "bash");
        assert_eq!(classification.tools[0].detail.as_deref(), Some("rg"));
        assert_eq!(classification.tools[0].call_id.as_deref(), Some("toolu_1"));
    }

    #[test]
    fn classifies_openai_tool_role_before_user_heuristics() {
        let body = json!({
            "messages": [
                {"role": "assistant", "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "pytest", "arguments": "{}"}
                }]},
                {"role": "tool", "tool_call_id": "call_1", "content": "passed"}
            ]
        });
        let classification = RequestClassifierFilter::classify_request(
            &Bytes::from(serde_json::to_vec(&body).unwrap()),
            "/v1/chat/completions",
        )
        .unwrap();
        assert_eq!(classification.content_type, ContentType::ToolResult);
        assert_eq!(classification.tools[0].name, "pytest");
        assert_eq!(classification.tools[0].call_id.as_deref(), Some("call_1"));
    }

    #[test]
    fn resolves_openai_tool_result_detail_from_call_arguments() {
        let body = json!({
            "messages": [
                {"role": "assistant", "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "bash", "arguments": "{\"command\":\"cd /tmp && rg TODO\"}"}
                }]},
                {"role": "tool", "tool_call_id": "call_1", "content": "matches"}
            ]
        });
        let classification = RequestClassifierFilter::classify_request(
            &Bytes::from(serde_json::to_vec(&body).unwrap()),
            "/v1/chat/completions",
        )
        .unwrap();
        assert_eq!(classification.tools[0].name, "bash");
        assert_eq!(classification.tools[0].detail.as_deref(), Some("rg"));
    }
}
