"""同一 staging 根只允许一个控制器；执行清理依赖单实例所有权。"""

import os


class ControllerLock:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.stream = (root / ".controller.lock").open("a+b")
        self.stream.seek(0)
        if not self.stream.read(1):
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)

    def acquire(self):
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("sandbox controller already owns staging root") from None

    def close(self):
        self.stream.close()
