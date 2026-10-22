"""上传读取共享工具（审计 A01）：分块、有界地读取上传内容。

各上传路由统一改用 read_upload_limited，替代裸 `await file.read()`——
先读后限的写法会让超大上传在长度检查之前就全额进入内存。超限抛
UploadTooLargeError（main.py 已全局映射 400），路由无需自行 try/except
即可得到明确的客户端错误。
"""

# 默认分片大小：1MB。足够小以及时中断，又足够大不显著增加系统调用次数
DEFAULT_CHUNK_SIZE = 1024 * 1024


class UploadTooLargeError(Exception):
    """上传内容超过限额。max_bytes 为生效上限，可映射 HTTP 400。"""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        super().__init__(f"上传内容超过 {max_bytes} 字节上限")


async def read_upload_limited(file, max_bytes: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> bytes:
    """分块读取上传内容，累计超过 max_bytes 即抛 UploadTooLargeError。

    file：任何提供 async read(size) 的对象（Starlette UploadFile 即符合）。
    每读一个分片立即累计校验：超限当场停止读取，不会把整个超大内容吞完
    再抛——分片限额的内存意义正在于此。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(max_bytes)
        chunks.append(chunk)
