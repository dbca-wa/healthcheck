import hashlib
import shutil
import json
import os
import aiofiles.os
import aiofiles
import logging

from .. import settings
from .. import utils

from .base import BaseHealthCheck,BaseServiceHealthCheckTask
from ..locks import FileLock

logger = logging.getLogger(__name__)

class EditingHealthCheck(BaseHealthCheck):
    def __init__(self,healthcheck,configfile):
        super().__init__(configfile=configfile)
        self.healthcheck = healthcheck
        self._lock = FileLock("{}.lock".format(self.configfile))
        self._continuouscheck_starttime = None

    @classmethod
    async def create_instance(cls,healthcheck,configfile):
        instance = cls(healthcheck,configfile)
        await instance.load_configs()
        await instance.load_checkingstatus()
        return instance

    async def reset(self):
        """
        Return True if changed; otherwise return False
        """
        async with self._lock:
            if await aiofiles.os.path.exists(self.healthcheck.configfile):
                await utils.copyfile(self.healthcheck.configfile,self.configfile)
            else:
                async with aiofiles.open(self.configfile,'w') as f:
                    await f.write("[]")

            async with aiofiles.open(self.configfile,'rb') as f:
                configs = await f.read()

            config_hashcode = hashlib.sha256(configs).hexdigest()
            return config_hashcode != self.config_hashcode

    async def continuous_check(self,*args,taskcls=BaseServiceHealthCheckTask):
        await super().continuous_check(*args,taskcls=taskcls)
        self._continuouscheck_starttime = utils.now()

    def stop_continuous_check(self):
        super().stop_continuous_check()
        self._continuouscheck_starttime = None

    async def _continuous_check(self,taskcls,*args):
        if self._continuouscheck_starttime and (utils.now() - self._continuouscheck_starttime).total_seconds() > settings.EDITINGHEALTHCHECK_CONTINUOUSCHECK_MAXTIME:
            #exceed the maximum time
            self.stop_continuous_check()
            return

        await super()._continuous_check(taskcls,*args)

    async def save(self,configtxt):
        """
        Return True if changed; otherwise return False
        """
        #parse
        async with self._lock:
            try:
                configs = json.loads(configtxt)
            except Exception as ex:
                raise Exception("Health check configuration is not a correct json object")
            #check the configuration
            data = self.init_configs(configs)
    
            #save the json file
            async with aiofiles.open(self.configfile,'w') as f:
                await f.write(configtxt)
    
            #read the json file
            async with aiofiles.open(self.configfile,'rb') as f:
                configs = await f.read()
            config_hashcode = hashlib.sha256(configs).hexdigest()
            return config_hashcode != self.config_hashcode

    async def publish(self,user,comments):
        #validate the healthcheck configuration file
        async with self._lock:
            try:
                async with aiofiles.open(self.configfile,'rb') as f:
                    configtxt = await f.read()
    
                config_hashcode = hashlib.sha256(configtxt).hexdigest()
                if config_hashcode == self.healthcheck.config_hashcode:
                    #not changed
                    changed = False
                    await utils.removefile(self.configfile)
                    return False
                    
                configs = json.loads(configtxt.decode())
            except Exception as ex:
                raise Excepton("Health check configuration is not a correct json object")
    
            data = self.init_configs(configs)
    
            #save the publish config file
            now = utils.now()
            configdir,configfilename = os.path.split(self.healthcheck.configfile)
            configfilebase,configfileext = os.path.splitext(configfilename)
            publishedfile = os.path.join(self.healthcheck.publishhistorydir,"{1}.{0}{2}".format(now.strftime("%Y%m%dT%H%M%S"),configfilebase,configfileext))

            await utils.copyfile(self.configfile,publishedfile)
    
            #update the publish history
            count = 0
            tmpfile = "{}.tmp".format(self.healthcheck.publishhistoryfile)
            async with aiofiles.open(tmpfile,'w') as fw:
                count += 1
                await fw.write(json.dumps([now.strftime("%Y-%m-%d %H:%M:%S"),os.path.basename(publishedfile),user,comments]))
                async with aiofiles.open(self.healthcheck.publishhistoryfile,'r') as fr:
                    while True:
                        line = await fr.readline()
                        if not line:
                            break
                        else:
                            count += 1
                            if count == 2:
                                await fw.write(os.linesep)
                            if count <= settings.HEALTHCHECK_PUBLISH_HISTORIES:
                                await fw.write(line)
                            else:
                                #remove the publish history
                                publisheddata = json.loads(line.strip())
                                if publisheddata[1]:
                                    await utils.removefile(os.path.join(self.healthcheck.publishhistorydir,publisheddata[1]))
            await aiofiles.os.rename(tmpfile,self.healthcheck.publishhistoryfile)
    
            #update the current healthceck config file
            await aiofiles.os.rename(self.configfile,self.healthcheck.configfile)
            return True
