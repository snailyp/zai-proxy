import base64
import time
import hmac
import hashlib


def generate_signature(
    t: str,
    e: str,
    r: int,
) -> dict:
    """
    根据输入参数 t, e, r 生成签名和时间戳。

    Args:
        t: 第一个输入参数。
        e: 第二个输入参数。
        r: 毫秒级时间戳。

    Returns:
        一个包含 'signature' 和 'timestamp' 的字典。
    """
    # 1. 使用传入的时间戳
    timestamp_ms = r
    # timestamp_ms = 1760369828098

    encoded_e = e.encode("utf-8")
    b64_encoded_e = base64.b64encode(encoded_e).decode("utf-8")

    # 2. 拼接字符串
    message_string = f"{t}|{b64_encoded_e}|{timestamp_ms}"

    # 3. 计算 n
    n = timestamp_ms // (5 * 60 * 1000)

    # 4. 计算中间密钥 o (HMAC-SHA256)
    key1 = "key-@@@@)))()((9))-xxxx&&&%%%%%".encode("utf-8")
    msg1 = str(n).encode("utf-8")
    intermediate_key = hmac.new(key1, msg1, hashlib.sha256).hexdigest()

    # 5. 计算最终签名 (HMAC-SHA256)
    key2 = intermediate_key.encode("utf-8")
    msg2 = message_string.encode("utf-8")
    final_signature = hmac.new(key2, msg2, hashlib.sha256).hexdigest()

    # 6. 返回结果
    return {"signature": final_signature, "timestamp": timestamp_ms}


if __name__ == "__main__":
    # 示例用法
    e_value = "requestId,eef12d6c-6dc9-47a0-aae8-b9f3454f98c5,timestamp,1761038714733,user_id,21ea9ec3-e492-4dbb-b522-fc0eaf64f0f6"
    t_value = "hi"
    # r_value = int(time.time() * 1000)
    r_value = 1761038714733
    result = generate_signature(e_value, t_value, r_value)
    print(f"生成的签名: {result['signature']}")
    print(f"时间戳: {result['timestamp']}")
