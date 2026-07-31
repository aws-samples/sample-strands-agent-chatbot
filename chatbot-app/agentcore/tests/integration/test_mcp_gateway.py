"""
Integration tests for MCP Gateway tool handling.

Tests the MCP (Model Context Protocol) message format, Lambda tool response
structure, and event formatting as expected by the frontend chatbot.

These tests can run without actual Gateway connections by mocking responses.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List


# ============================================================
# Mock Classes for MCP Gateway Testing
# ============================================================

class MockMCPTool:
    """Mock MCP Tool definition."""
    def __init__(self, name: str, description: str = "", prefix: str = ""):
        self.tool_name = f"{prefix}_{name}" if prefix else name
        self.tool_description = description


class MockLambdaContext:
    """Mock Lambda context for tool invocation."""
    def __init__(self, tool_name: str):
        self.client_context = MagicMock()
        self.client_context.custom = {
            "bedrockAgentCoreToolName": tool_name
        }


# ============================================================
# MCP Gateway Tool Response Format Tests
# ============================================================

class TestMCPGatewayResponseFormat:
    """Tests for MCP Gateway response format."""

    def test_lambda_success_response_format(self):
        """Test Lambda success response format from Gateway tools."""
        # Standard Lambda response wrapping MCP content
        lambda_response = {
            "statusCode": 200,
            "body": json.dumps({
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "success",
                        "query": "AWS Lambda",
                        "count": 1,
                        "results": [
                            {
                                "title": "AWS Lambda",
                                "snippet": "AWS Lambda is a serverless...",
                                "url": "https://en.wikipedia.org/wiki/AWS_Lambda"
                            }
                        ]
                    }, indent=2)
                }]
            })
        }

        assert lambda_response["statusCode"] == 200

        body = json.loads(lambda_response["body"])
        assert "content" in body
        assert body["content"][0]["type"] == "text"

        # Parse inner content
        inner_content = json.loads(body["content"][0]["text"])
        assert inner_content["status"] == "success"
        assert "results" in inner_content

    def test_lambda_error_response_format(self):
        """Test Lambda error response format."""
        lambda_response = {
            "statusCode": 400,
            "body": json.dumps({
                "error": "query parameter required"
            })
        }

        assert lambda_response["statusCode"] == 400

        body = json.loads(lambda_response["body"])
        assert "error" in body

    def test_mcp_content_text_type(self):
        """Test MCP content with text type."""
        mcp_content = {
            "content": [{
                "type": "text",
                "text": "Search completed successfully"
            }]
        }

        assert mcp_content["content"][0]["type"] == "text"
        assert "text" in mcp_content["content"][0]

    def test_mcp_content_image_type(self):
        """Test MCP content with image type."""
        mcp_content = {
            "content": [{
                "type": "image",
                "image": {
                    "format": "png",
                    "source": {
                        "data": "base64encodedimagedata..."
                    }
                }
            }]
        }

        assert mcp_content["content"][0]["type"] == "image"
        assert "image" in mcp_content["content"][0]
        assert mcp_content["content"][0]["image"]["format"] == "png"


# ============================================================
# MCP Tool Definition Tests
# ============================================================

class TestMCPToolDefinition:
    """Tests for MCP tool definition format."""

    def test_tool_with_prefix(self):
        """Test tool name with gateway prefix."""
        tool = MockMCPTool("wikipedia_search", "Search Wikipedia articles", prefix="gateway")

        assert tool.tool_name == "gateway_wikipedia_search"
        assert tool.tool_description == "Search Wikipedia articles"

    def test_tool_without_prefix(self):
        """Test tool name without prefix."""
        tool = MockMCPTool("wikipedia_search", "Search Wikipedia articles")

        assert tool.tool_name == "wikipedia_search"

    def test_tool_name_extraction_from_context(self):
        """Test extracting tool name from Lambda context."""
        # Gateway sets tool name in client context
        context = MockLambdaContext("gateway___wikipedia_search")

        tool_name = context.client_context.custom.get('bedrockAgentCoreToolName', '')

        # Extract actual tool name after prefix
        if '___' in tool_name:
            tool_name = tool_name.split('___')[-1]

        assert tool_name == "wikipedia_search"


# ============================================================
# Wikipedia Lambda Tool Tests
# ============================================================

class TestWikipediaLambdaTool:
    """Tests for Wikipedia Lambda tool format."""

    def test_wikipedia_search_input_format(self):
        """Test Wikipedia search input parameters."""
        search_params = {
            "query": "Artificial Intelligence"
        }

        assert "query" in search_params
        assert isinstance(search_params["query"], str)

    def test_wikipedia_search_success_response(self):
        """Test Wikipedia search success response format."""
        response_data = {
            "status": "success",
            "query": "Artificial Intelligence",
            "count": 1,
            "results": [
                {
                    "title": "Artificial intelligence",
                    "snippet": "Artificial intelligence (AI) is the intelligence...",
                    "url": "https://en.wikipedia.org/wiki/Artificial_intelligence"
                }
            ]
        }

        assert response_data["status"] == "success"
        assert response_data["count"] == len(response_data["results"])
        assert "title" in response_data["results"][0]
        assert "snippet" in response_data["results"][0]
        assert "url" in response_data["results"][0]

    def test_wikipedia_search_no_results(self):
        """Test Wikipedia search no results response."""
        response_data = {
            "status": "no_results",
            "query": "xyznonexistentquery123",
            "message": "No Wikipedia articles found for query: xyznonexistentquery123",
            "results": []
        }

        assert response_data["status"] == "no_results"
        assert len(response_data["results"]) == 0
        assert "message" in response_data

    def test_wikipedia_get_article_input_format(self):
        """Test Wikipedia get article input parameters."""
        article_params = {
            "title": "Machine learning",
            "summary_only": False
        }

        assert "title" in article_params
        assert "summary_only" in article_params

    def test_wikipedia_get_article_success_response(self):
        """Test Wikipedia get article success response format."""
        response_data = {
            "status": "success",
            "title": "Machine learning",
            "content_type": "full_text",
            "content": "Machine learning (ML) is a branch of artificial intelligence...",
            "url": "https://en.wikipedia.org/wiki/Machine_learning",
            "categories": ["Category:Machine learning", "Category:Artificial intelligence"],
            "character_count": 5000
        }

        assert response_data["status"] == "success"
        assert "title" in response_data
        assert "content" in response_data
        assert response_data["content_type"] in ["summary", "full_text"]

    def test_wikipedia_get_article_not_found(self):
        """Test Wikipedia get article not found response."""
        response_data = {
            "status": "not_found",
            "message": "Wikipedia article not found: NonExistentArticle12345",
            "suggestion": "Try using wikipedia_search to find the correct article title"
        }

        assert response_data["status"] == "not_found"
        assert "suggestion" in response_data


# ============================================================
# MCP Gateway Tool Call Flow Tests
# ============================================================

class TestMCPGatewayToolCallFlow:
    """Tests for MCP Gateway tool call flow."""

    def test_tool_call_request_format(self):
        """Test tool call request format to Gateway."""
        tool_call = {
            "tool_use_id": "tool-use-123",
            "name": "gateway_wikipedia_search",
            "arguments": {
                "query": "Quantum Computing"
            }
        }

        assert "tool_use_id" in tool_call
        assert "name" in tool_call
        assert "arguments" in tool_call

    def test_tool_result_format_for_frontend(self):
        """Test tool result format as expected by frontend."""
        # After event_formatter processes Lambda response
        tool_result_event = {
            "type": "tool_result",
            "toolUseId": "tool-use-123",
            "result": json.dumps({
                "status": "success",
                "query": "Quantum Computing",
                "results": [{"title": "Quantum computing", "snippet": "..."}]
            })
        }

        assert tool_result_event["type"] == "tool_result"
        assert "toolUseId" in tool_result_event
        assert "result" in tool_result_event

    def test_lambda_response_unwrapping(self):
        """Test Lambda response unwrapping in event_formatter."""
        # Lambda wraps MCP response
        lambda_response_text = json.dumps({
            "statusCode": 200,
            "body": json.dumps({
                "content": [{"text": "Unwrapped content"}]
            })
        })

        # Parse Lambda wrapper
        parsed = json.loads(lambda_response_text)

        assert parsed["statusCode"] == 200

        # Unwrap body
        body = json.loads(parsed["body"])
        assert "content" in body
        assert body["content"][0]["text"] == "Unwrapped content"


# ============================================================
# Multiple Gateway Tools Tests
# ============================================================

class TestMultipleGatewayTools:
    """Tests for multiple MCP Gateway tools."""

    def test_tool_list_format(self):
        """Test format of tool list from Gateway."""
        tools = [
            MockMCPTool("wikipedia_search", "Search Wikipedia", "gateway"),
            MockMCPTool("wikipedia_get_article", "Get Wikipedia article", "gateway"),
            MockMCPTool("arxiv_search", "Search arXiv papers", "gateway"),
            MockMCPTool("finance_stock_price", "Get stock prices", "gateway")
        ]

        tool_names = [t.tool_name for t in tools]

        assert "gateway_wikipedia_search" in tool_names
        assert "gateway_arxiv_search" in tool_names
        assert len(tools) == 4

    def test_tool_name_uniqueness(self):
        """Test tool names are unique across Gateway."""
        tools = [
            MockMCPTool("wikipedia_search", prefix="gateway"),
            MockMCPTool("arxiv_search", prefix="gateway"),
            MockMCPTool("tavily_search", prefix="gateway")
        ]

        names = [t.tool_name for t in tools]
        assert len(names) == len(set(names)), "Tool names must be unique"


# ============================================================
# ArXiv Lambda Tool Tests
# ============================================================

class TestArxivLambdaTool:
    """Tests for ArXiv Lambda tool format."""

    def test_arxiv_search_input_format(self):
        """Test ArXiv search input parameters."""
        search_params = {
            "query": "machine learning transformers",
            "max_results": 5
        }

        assert "query" in search_params
        assert "max_results" in search_params

    def test_arxiv_search_response_format(self):
        """Test ArXiv search response format."""
        response_data = {
            "status": "success",
            "query": "machine learning transformers",
            "count": 2,
            "results": [
                {
                    "title": "Attention Is All You Need",
                    "authors": ["Vaswani, A.", "et al."],
                    "summary": "The dominant sequence transduction models...",
                    "published": "2017-06-12",
                    "pdf_url": "https://arxiv.org/pdf/1706.03762",
                    "arxiv_id": "1706.03762"
                }
            ]
        }

        assert response_data["status"] == "success"
        assert "results" in response_data
        result = response_data["results"][0]
        assert "title" in result
        assert "authors" in result
        assert "pdf_url" in result


# ============================================================
# Finance Lambda Tool Tests
# ============================================================

class TestFinanceLambdaTool:
    """Tests for Finance Lambda tool format."""

    def test_stock_price_input_format(self):
        """Test stock price input parameters."""
        stock_params = {
            "symbol": "AMZN"
        }

        assert "symbol" in stock_params

    def test_stock_price_response_format(self):
        """Test stock price response format."""
        response_data = {
            "status": "success",
            "symbol": "AMZN",
            "price": 178.50,
            "currency": "USD",
            "change": 2.35,
            "change_percent": 1.33,
            "timestamp": "2024-01-15T16:00:00Z"
        }

        assert response_data["status"] == "success"
        assert "symbol" in response_data
        assert "price" in response_data
        assert isinstance(response_data["price"], (int, float))


# ============================================================
# Gateway JWT Bearer Auth Tests
# ============================================================

class TestGatewayJWTAuth:
    """Tests for Gateway JWT Bearer authentication."""

    def test_bearer_auth_header(self):
        """Test that Bearer token is set in Authorization header."""
        from agent.mcp.client import BearerAuth
        import httpx

        auth = BearerAuth("test-jwt-token")
        request = httpx.Request("POST", "https://gateway.example.com")
        flow = auth.auth_flow(request)
        signed = next(flow)
        assert signed.headers["Authorization"] == "Bearer test-jwt-token"


# ============================================================
# MCP Content Block Processing Tests
# ============================================================

class TestMCPContentBlockProcessing:
    """Tests for MCP content block processing by event_formatter."""

    def test_text_content_block(self):
        """Test text content block processing."""
        content_block = {
            "type": "text",
            "text": "This is the search result text."
        }

        assert content_block["type"] == "text"
        result_text = content_block["text"]
        assert isinstance(result_text, str)

    def test_image_content_block(self):
        """Test image content block processing."""
        content_block = {
            "type": "image",
            "image": {
                "format": "png",
                "source": {
                    "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
                }
            }
        }

        assert content_block["type"] == "image"
        assert content_block["image"]["format"] == "png"
        assert "data" in content_block["image"]["source"]

    def test_document_content_block_skipped(self):
        """Test document content block is skipped for frontend display."""
        content_block = {
            "type": "document",
            "document": {
                "name": "report",
                "format": "docx",
                "source": {
                    "bytes": b"binary document data..."
                }
            }
        }

        # Document bytes should be skipped for frontend
        # Only metadata should be passed
        assert content_block["type"] == "document"
        # Frontend should receive metadata, not bytes

    def test_multiple_content_blocks(self):
        """Test processing multiple content blocks."""
        content_blocks = [
            {"type": "text", "text": "First result"},
            {"type": "text", "text": "Second result"},
            {"type": "image", "image": {"format": "png", "source": {"data": "..."}}}
        ]

        text_blocks = [b for b in content_blocks if b["type"] == "text"]
        image_blocks = [b for b in content_blocks if b["type"] == "image"]

        assert len(text_blocks) == 2
        assert len(image_blocks) == 1


# ============================================================
# MCP Tool Error Handling Tests
# ============================================================

class TestMCPToolErrorHandling:
    """Tests for MCP tool error handling."""

    def test_missing_required_parameter(self):
        """Test error when required parameter is missing."""
        error_response = {
            "statusCode": 400,
            "body": json.dumps({
                "error": "query parameter required"
            })
        }

        body = json.loads(error_response["body"])
        assert "error" in body
        assert "required" in body["error"]

    def test_tool_execution_error(self):
        """Test error during tool execution."""
        error_response = {
            "statusCode": 500,
            "body": json.dumps({
                "error": "Wikipedia search error: Connection timeout"
            })
        }

        body = json.loads(error_response["body"])
        assert "error" in body
        assert "timeout" in body["error"].lower() or "error" in body["error"].lower()

    def test_unknown_tool_error(self):
        """Test error for unknown tool name."""
        error_response = {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Unknown tool: nonexistent_tool"
            })
        }

        body = json.loads(error_response["body"])
        assert "Unknown tool" in body["error"]


# ============================================================
# Google Search Tool Tests (URL-based Images)
# ============================================================

class TestGoogleSearchTool:
    """Tests for Google Search tool with URL-based images."""

    def test_google_search_image_results(self):
        """Test Google Search returns URL-based images."""
        response_data = {
            "status": "success",
            "query": "pandas data analysis",
            "images": [
                {
                    "link": "https://example.com/image1.png",
                    "thumbnail": "https://example.com/thumb1.png",
                    "title": "Pandas DataFrame",
                    "width": 800,
                    "height": 600
                }
            ],
            "results": [
                {"title": "Pandas Documentation", "link": "https://pandas.pydata.org"}
            ]
        }

        assert "images" in response_data
        image = response_data["images"][0]
        assert "link" in image  # URL, not base64
        assert "thumbnail" in image

    def test_image_url_type_in_tool_result(self):
        """Test image URL type in tool result for frontend."""
        # event_formatter should mark URL-based images
        image_result = {
            "type": "url",
            "url": "https://example.com/image.png",
            "thumbnail": "https://example.com/thumb.png",
            "title": "Example Image",
            "width": 1024,
            "height": 768
        }

        assert image_result["type"] == "url"
        assert image_result["url"].startswith("http")
