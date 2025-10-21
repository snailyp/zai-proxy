from datetime import datetime
import json
import time
from typing import Any, Dict, List, Optional
import uuid
import httpx
from api.config import get_settings
from api.image_uploader import ImageUploader
from api.models import ChatRequest, Message
from api.logger import setup_logger
from api.signature_generator import generate_signature

logger = setup_logger(__name__)

settings = get_settings()
BASE_URL = settings.PROXY_URL


def create_chat_completion_data(
    content: str,
    model: str,
    timestamp: int,
    phase: str,
    usage: Optional[dict] = None,
    finish_reason: Optional[str] = None,
) -> Dict[str, Any]:
    if phase == "answer":
        finish_reason = None
        delta = {"content": content, "role": "assistant"}
    elif phase == "thinking":
        finish_reason = None
        delta = {"reasoning_content": content, "role": "assistant"}
    elif phase == "other":
        finish_reason = finish_reason
        delta = {"content": content, "role": "assistant"}
    elif phase == "tool_call":
        finish_reason = None
        delta = {"content": content, "role": "assistant"}

    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion.chunk",
        "created": timestamp,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
        "usage": None if usage is None else usage,
    }


def convert_messages(messages: List[Message]):
    trans_messages = []
    image_urls = []
    for message in messages:
        if isinstance(message.content, str):
            trans_messages.append({"role": message.role, "content": message.content})
        elif isinstance(message.content, list):
            for part in message.content:
                if part.get("type") == "text":
                    trans_messages.append(
                        {"role": "user", "content": part.get("text", "")}
                    )
                elif part.get("type") == "image_url":
                    image_urls.append(part.get("image_url", "").get("url", ""))
    return {"messages": trans_messages, "image_urls": image_urls}


def getfeatures(model: str, streaming: bool) -> Dict[str, bool]:
    dict = {}
    if streaming:
        features = {
            "image_generation": False,
            "web_search": False,
            "auto_web_search": False,
            "preview_mode": False,
            "flags": [],
            "enable_thinking": True,
        }

        mcp_servers = []
        if model in ["glm-4.6-search", "glm-4.6-advanced-search"]:
            features["web_search"] = True
            features["auto_web_search"] = True
            features["preview_mode"] = True
        if model == "glm-4.6-nothinking":
            features["enable_thinking"] = False
        if model == "glm-4.6-advanced-search":
            mcp_servers = [
                "advanced-search",
            ]

        dict["features"] = features
        dict["mcp_servers"] = mcp_servers
    else:
        features = {
            "image_generation": False,
            "web_search": False,
            "auto_web_search": False,
            "preview_mode": False,
            "flags": [],
            "enable_thinking": False,
        }
        mcp_servers = []
        dict["features"] = features
        dict["mcp_servers"] = mcp_servers
    return dict


async def prepare_data(request, access_token, streaming=True):
    convert_dict = convert_messages(request.messages)
    zai_data = {
        "stream": True,
        "model": settings.MODELS_MAPPING.get(request.model),
        "messages": convert_dict["messages"],
        "chat_id": str(uuid.uuid4()),
        "id": str(uuid.uuid4()),
    }

    image_uploader = ImageUploader(access_token)
    files = []
    for url in convert_dict["image_urls"]:
        if url.startswith("data:image/"):
            image_base64 = url.split("base64,")[-1]
            pic_id = await image_uploader.upload_base64_image(image_base64)
            files.append({"type": "image", "id": pic_id})
        elif url.startswith("http"):
            pic_id = await image_uploader.upload_image_from_url(url)
            files.append({"type": "image", "id": pic_id})
    zai_data["files"] = files

    features_dict = getfeatures(request.model, streaming)
    zai_data["features"] = features_dict["features"]
    if len(features_dict["mcp_servers"]) > 0:
        zai_data["mcp_servers"] = features_dict["mcp_servers"]

    params = {
        "requestId": str(uuid.uuid4()),
        "timestamp": str(int(time.time() * 1000)),
        "user_id": str(uuid.uuid4()),
    }

    t = "requestId,{request_id},timestamp,{timestamp},user_id,{user_id}".format(
        request_id=params["requestId"],
        timestamp=int(params["timestamp"]),
        user_id=params["user_id"],
    )

    e = zai_data["messages"][-1]["content"]
    r = int(time.time() * 1000)
    signature_data = generate_signature(t, e, r)
    params["signature_timestamp"] = str(signature_data["timestamp"])
    headers = settings.HEADERS
    headers["Authorization"] = f"Bearer {access_token}"
    headers["X-Signature"] = signature_data["signature"]
    return zai_data, params, headers


async def process_streaming_response(request: ChatRequest, access_token: str):

    zai_data, params, headers = await prepare_data(request, access_token)
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST",
                f"{BASE_URL}/api/chat/completions",
                headers=headers,
                params=params,
                json=zai_data,
                timeout=300,
            ) as response:
                response.raise_for_status()
                timestamp = int(datetime.now().timestamp())
                async for line in response.aiter_lines():
                    if line:
                        # print(line)
                        if line.startswith("data:"):
                            json_str = line[6:]  # 去掉 "data: " 前缀
                            json_object = json.loads(json_str)
                            if json_object.get("data", {}).get("phase") == "thinking":
                                if json_object.get("data").get(
                                    "delta_content"
                                ) and "</summary>\n" in json_object.get("data").get(
                                    "delta_content"
                                ):
                                    content = (
                                        json_object.get("data")
                                        .get("delta_content", "")
                                        .split("</summary>\n")[-1]
                                    )
                                else:
                                    content = json_object.get("data").get(
                                        "delta_content", ""
                                    )
                                yield f"data: {json.dumps(create_chat_completion_data(content, request.model, timestamp, 'thinking'))}\n\n"
                            elif json_object.get("data", {}).get("phase") == "answer":
                                if json_object.get("data").get(
                                    "edit_content"
                                ) and "</summary>\n" in json_object.get("data").get(
                                    "edit_content"
                                ):
                                    content = (
                                        json_object.get("data")
                                        .get("edit_content", "")
                                        .split("</details>")[-1]
                                    )
                                elif json_object.get("data").get("delta_content"):
                                    content = json_object.get("data").get(
                                        "delta_content"
                                    )
                                yield f"data: {json.dumps(create_chat_completion_data(content, request.model, timestamp, 'answer'))}\n\n"
                            elif json_object.get("data", {}).get("phase") == "other":
                                usage = json_object.get("data").get("usage", {})
                                content = json_object.get("data").get(
                                    "delta_content", ""
                                )
                                yield f"data: {json.dumps(create_chat_completion_data(content, request.model, timestamp, 'other', usage, 'stop'))}\n\n"
                            # elif json_object.get("data", {}).get("phase") == "tool_call":
                            #     content = json_object.get("data").get(
                            #         "edit_content", ""
                            #     )
                            #     yield f"data: {json.dumps(create_chat_completion_data(content, request.model, timestamp, 'tool_call'))}\n\n"
                            elif json_object.get("data", {}).get("phase") == "done":
                                yield "data: [DONE]\n\n"
                                break

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error occurred: {e}")
        except httpx.RequestError as e:
            logger.error(f"Error occurred during request: {e}")


async def process_non_streaming_response(request: ChatRequest, access_token: str):

    zai_data, params, headers = await prepare_data(request, access_token, False)
    full_response = ""
    usage = {}
    async with httpx.AsyncClient() as client:
        async with client.stream(
            method="POST",
            url=f"{BASE_URL}/api/chat/completions",
            headers=headers,
            params=params,
            json=zai_data,
            timeout=300,
        ) as response:
            async for line in response.aiter_lines():
                if line:
                    if line.startswith("data:"):
                        json_str = line[6:]  # 去掉 "data: " 前缀
                        json_object = json.loads(json_str)
                        if json_object.get("data", {}).get("phase") == "answer":
                            if json_object.get("data").get("delta_content"):
                                content = json_object.get("data").get("delta_content")
                            else:
                                content = ""
                            full_response += content
                        elif json_object.get("data", {}).get("phase") == "other":
                            usage = json_object.get("data").get("usage", {})
                            content = json_object.get("data").get("delta_content", "")
                            full_response += content
    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(datetime.now().timestamp()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": full_response},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }
