"""原始资料的受控保存与可信回读。

原件放在仓库之外的私有目录，按内容 SHA-256 寻址：
同一个哈希只对应一份内容，已存在的内容不会被静默覆盖；
每次回读都重新计算哈希，内容对不上就拒绝，不返回"看起来像"的证据。
"""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from app.core.errors import APIError

_SHA256 = re.compile(r"[0-9a-f]{64}")


class OriginalStore:
    """内容寻址的原件仓库。只供服务端使用，不对外暴露下载接口。"""

    def __init__(self, root: Path) -> None:
        # 只登记位置，不建目录：应用启动阶段不得隐式初始化任何存储
        self.root = Path(root)

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._restrict(self.root, 0o700)

    @staticmethod
    def _restrict(path: Path, mode: int) -> None:
        if os.name != "nt":
            path.chmod(mode)

    def path_for(self, sha256: str) -> Path:
        if not _SHA256.fullmatch(sha256):
            raise APIError(422, "VALIDATION_ERROR", "原件摘要必须是 64 位十六进制 SHA-256")
        # 两级分片，避免单目录堆积过多文件
        return self.root / sha256[:2] / sha256[2:4] / f"{sha256}.zip"

    def _verify(self, path: Path, sha256: str, *, code: str, message: str) -> None:
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            raise APIError(409, code, message)

    def put(self, sha256: str, payload: bytes) -> bool:
        """保存原件；返回 True 表示本次新建，False 表示同内容已存在。

        写入失败时清理临时文件；已有内容哈希不符时拒绝，不覆盖。
        """
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise APIError(422, "ORIGINAL_DIGEST_MISMATCH", "原件内容与登记的摘要不一致")
        self._ensure_root()
        target = self.path_for(sha256)
        if target.exists():
            self._verify(
                target,
                sha256,
                code="ORIGINAL_CONTENT_CONFLICT",
                message="受控存储中同摘要的原件内容不一致，拒绝覆盖",
            )
            return False

        target.parent.mkdir(parents=True, exist_ok=True)
        self._restrict(target.parent, 0o700)
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".upload-", suffix=".part")
        temporary_path = Path(temporary)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._restrict(temporary_path, 0o600)
            self._verify(
                temporary_path,
                sha256,
                code="ORIGINAL_WRITE_FAILED",
                message="原件落盘后校验失败，已丢弃本次写入",
            )
            if target.exists():
                # 并发写入同一内容：保留先到的一份，仍然校验它
                self._verify(
                    target,
                    sha256,
                    code="ORIGINAL_CONTENT_CONFLICT",
                    message="受控存储中同摘要的原件内容不一致，拒绝覆盖",
                )
                return False
            os.replace(temporary_path, target)
            return True
        finally:
            temporary_path.unlink(missing_ok=True)

    def read(self, sha256: str) -> bytes:
        """回读原件并校验哈希；缺失或被篡改都明确拒绝。"""
        target = self.path_for(sha256)
        if not target.is_file():
            raise APIError(409, "ORIGINAL_UNAVAILABLE", "原件不在受控存储中，无法作为证据回读")
        payload = target.read_bytes()
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise APIError(409, "SOURCE_CHANGED", "原件内容与登记摘要不一致，拒绝作为证据回读")
        return payload

    def discard(self, sha256: str) -> None:
        """回滚本次导入新建的原件；只用于失败清理，不作为常规删除入口。"""
        self.path_for(sha256).unlink(missing_ok=True)
