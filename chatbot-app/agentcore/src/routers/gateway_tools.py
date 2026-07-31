"""
Gateway Tools API Router
Provides endpoints to discover and manage Gateway MCP tools
"""

import logging
from fastapi import APIRouter
from typing import Dict, Any
from agent.mcp.client import create_gateway_mcp_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gateway-tools", tags=["gateway-tools"])


@router.get("/list")
async def list_gateway_tools() -> Dict[str, Any]:
    """
    Get list of available tools from AgentCore Gateway.

    Returns:
        Dictionary with tools list and metadata

    Example response:
        {
            "success": true,
            "gateway_url": "https://...",
            "tools": [
                {
                    "id": "gateway_wikipedia-search___wikipedia_search",
                    "name": "wikipedia_search",
                    "description": "Search Wikipedia articles",
                    "category": "search"
                },
                ...
            ],
            "count": 12
        }
    """
    try:
        # Create Gateway MCP client (without filtering)
        client = create_gateway_mcp_client()

        if not client:
            return {
                "success": False,
                "error": "Gateway not available",
                "tools": [],
                "count": 0
            }

        # Get tools from Gateway
        # Using manual context management for API endpoint
        with client:
            tools = client.list_tools_sync()

            # Convert to frontend-friendly format
            tools_list = []
            for tool in tools:
                tool_info = {
                    "id": tool.tool_name,  # Format: gateway_{target}___{tool}
                    "name": tool.tool_name.split("___")[-1] if "___" in tool.tool_name else tool.tool_name,
                    "full_name": tool.tool_name,
                    "description": getattr(tool, 'tool_description', 'Gateway MCP tool'),
                    "category": "gateway",
                    "enabled": False  # Default to disabled, frontend will manage state
                }

                # Try to categorize based on tool name
                tool_name_lower = tool_info["name"].lower()
                if "wikipedia" in tool_name_lower:
                    tool_info["category"] = "knowledge"
                elif "arxiv" in tool_name_lower:
                    tool_info["category"] = "research"
                elif "place" in tool_name_lower or "direction" in tool_name_lower or "geocode" in tool_name_lower:
                    tool_info["category"] = "maps"
                elif "google" in tool_name_lower or "search" in tool_name_lower or "tavily" in tool_name_lower:
                    tool_info["category"] = "search"
                elif "stock" in tool_name_lower or "financ" in tool_name_lower:
                    tool_info["category"] = "finance"

                tools_list.append(tool_info)

            logger.debug(f" Retrieved {len(tools_list)} tools from Gateway")

            return {
                "success": True,
                "gateway_url": "configured",  # Don't expose full URL to frontend
                "tools": tools_list,
                "count": len(tools_list)
            }

    except Exception as e:
        logger.error(f" Failed to list Gateway tools: {e}")
        import traceback
        logger.error(traceback.format_exc())

        return {
            "success": False,
            "error": str(e),
            "tools": [],
            "count": 0
        }


@router.get("/status")
async def get_gateway_status() -> Dict[str, Any]:
    """
    Check if Gateway is available and configured.

    Returns:
        Dictionary with Gateway status

    Example response:
        {
            "available": true,
            "configured": true,
            "error": null
        }
    """
    try:
        from agent.mcp.client import get_gateway_url

        gateway_url = get_gateway_url()

        if gateway_url:
            return {
                "available": True,
                "configured": True,
                "error": None
            }
        else:
            return {
                "available": False,
                "configured": False,
                "error": "Gateway URL not found in SSM Parameter Store"
            }

    except Exception as e:
        logger.error(f" Failed to check Gateway status: {e}")

        return {
            "available": False,
            "configured": False,
            "error": str(e)
        }
