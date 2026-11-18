import fcntl
import asyncio
import sys
import os
import aiofiles

from . import utils
from . import timezone

class FileLock(object):
    def __init__(self,file):
        self.file = file
        self._fd = None

    async def lock(self):
        if self._fd:
            raise Exception("Already acquired the lock({})".format(self.file))
        try:
            self._fd = await aiofiles.open(self.file,'w')
            fcntl.flock(self._fd.fileno(),fcntl.LOCK_EX)
        except FileNotFoundError as ex:
            await utils.makedir(os.path.dirname(self.file))
            await self.lock()


    async def release(self):
        if self._fd:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            finally:
                try:
                    await self._fd.close()
                except Exception as ex:
                    pass
                finally:
                    self._fd = None

    async def __aenter__(self):
        await self.lock()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.release()
        return False if exc_type else True

async def test(f,seconds):
    print("{}: Begin to acquire the lock({})".format(timezone.format_datetime(),f))
    async with FileLock(f) as lck:
        print("{}: Acquire the lock({}),begin to perform the task".format(timezone.format_datetime(),f))
        await asyncio.sleep(seconds)
        print("{}: End to perform the task, release the lock".format(timezone.format_datetime()))

    print("{}: Lock({}) released".format(timezone.format_datetime(),f))


if __name__ == '__main__':
    try:
        asyncio.run(test(sys.argv[1] if len(sys.argv) >= 2  else "/tmp/test.lck",int(sys.argv[2]) if len(sys.argv) == 3 else 10))
    except asyncio.CancelledError as ex:
        pass

