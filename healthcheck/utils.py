import shutil
import logging
import asyncio
import aiofiles.os
from concurrent.futures import ThreadPoolExecutor

from . import settings

logger = logging.getLogger(__name__)

async def ainput(prompt: str = "") -> str:
    """Asynchronously get user input."""
    try:
        with ThreadPoolExecutor(1, "AsyncInput") as executor:
            return await asyncio.get_event_loop().run_in_executor(executor, input, prompt)
    except EOFError as ex:
        await shutdown.process_userinterruption()

async def copyfile(source,target):
    await asyncio.to_thread(shutil.copyfile, source,target)

async def removefile(f,ignore_exception=True):
    try:
        await aiofiles.os.remove(f)
    except FileNotFoundError as ex:
        pass
    except Exception as ex:
        if ignore_exception:
            logger.error("Failed to remove the file '{}'. {} : {}".format(f,ex.__class__.__name__,str(ex)))
        else:
            raise

async def makedir(folder):
    try:
        await aiofiles.os.makedirs(folder)
    except FileExistsError as ex:
        if not await aiofiles.os.path.isdir(folder):
            raise IsADirectoryError("The folder path({}) is not a directory".format(folder))

_delete_nonempty_dir_supported = None
async def deletedir(folder):
    global  _delete_nonempty_dir_supported
    if _delete_nonempty_dir_supported is None or _delete_nonempty_dir_supported:
        try:
            await asyncio.to_thread(shutil.rmtree, folder)
            _delete_nonempty_dir_supported = True
            return
        except FileNotFoundError as ex:
            return
        except Exception as ex:
            _delete_nonempty_dir_supported = False
            raise Exception("Failed to remove the folder({}).{}: {}".format(folder,ex.__class__.__name__,str(ex)))

    if _delete_nonempty_dir_supported is False:
        #don't support delete folder
        #delete all files in the folder first.
        for name in await aiofiles.os.listdir(folder):
            f = os.path.join(folder,name)
            if await aiofiles.os.path.isfile(f):
                #is file, delete
                await aiofiles.os.remove(f)
            else:
                await deletedir(f)
        #delete the folder itself
        await aiofiles.os.rmdir(folder)

