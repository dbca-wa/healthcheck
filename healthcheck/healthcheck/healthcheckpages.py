import json
import os
import aiofiles
import aiofiles.os
import logging
#import urllib3
from datetime import datetime,timedelta


from .. import settings
from .. import utils
from .. import serializers
from ..locks import FileLock

logger = logging.getLogger(__name__)


class HealthCheckStatus(object):
    @staticmethod
    def serialize(data):
        return json.dumps(data,cls=serializers.JSONFormater)

    @staticmethod
    def deserialize(data):
        """
        data:a json string with pattern: [check start,check end,health status, msg,prtgdata, persistent]
        """
        if not data:
            return None
        data = data.strip()
        if not data:
            return None
        try:
            checkstatus = json.loads(data)
            checkstatus[0] = utils.parse_datetime(checkstatus[0])
            checkstatus[1] = utils.parse_datetime(checkstatus[1])
        except Exception as ex:
            raise Exception("The healthcheck status({}) is corrupted".format(data))

        return checkstatus

class HealthCheckPage(object):
    _last_greenhealthcheck = None
    _last_yellowhealthcheck = None
    _last_greenyellowhealthcheck = None
    _last_redhealthcheck = None
    _last_errorhealthcheck = None

    def __init__(self,healthcheckpages,starttime,filepath):
        self._healthcheckpages = healthcheckpages
        self._starttime = starttime
        self._filepath = filepath
        self._size = None
        self._last_healthcheck = None

    def __str__(self):
        return "starttime={} , filepath={}".format(self.starttime,self.filepath)

    @property
    def pageid(self):
        return int(self._starttime.timestamp())

    @property
    def starttime(self):
        return self._starttime

    @property
    def filepath(self):
        return self._filepath

    async def get_last_healthcheck(self):
        if self._size is None:
            await self._load()
        return self._last_healthcheck
 
    async def get_last_greenhealthcheck(self):
        if self._size is None:
            await self._load()
        return self._last_greenhealthcheck

    async def get_last_yellowhealthcheck(self):
        if self._size is None:
            await self._load()
        return self._last_yellowhealthcheck

    async def get_last_greenyellowhealthcheck(self):
        if self._size is None:
            await self._load()
        return self._last_greenyellowhealthcheck

    async def get_last_redhealthcheck(self):
        if self._size is None:
            await self._load()
        return self._last_redhealthcheck

    async def get_last_errorhealthcheck(self):
        if self._size is None:
            await self._load()
        return self._last_errorhealthcheck

    async def delete(self):
        await utils.removefile(self._filepath,ignore_exception=False)

    def serialize(self):
        return json.dumps([self._starttime.strftime("%Y-%m-%dT%H:%M:%S.%f"),self._filepath[len(self._healthcheckpages.basedir) + 1:]])

    @classmethod
    def deserialize(cls,healthcheckpages,data):
        try:
            pageindexdata = json.loads(data)
            pageindexdata[0] = utils.parse_datetime(pageindexdata[0])
            pageindexdata[1] = os.path.join(healthcheckpages.basedir,pageindexdata[1])
            return cls(healthcheckpages,*pageindexdata)
        except Exception as ex:
            raise Exception("The page index data({}) is corrupted".format(data))

    @property
    async def size(self):
        if self._size is None:
            await self._load()
        return self._size

    async def get_detailfile(self,starttime):
        return await self._healthcheckpages.get_detailfile(starttime)

    async def _load(self):
        if self._size is not None:
            return

        try:
            size = 0
            async with aiofiles.open(self._filepath,'r') as f:
                while True:
                    data = await f.readline()
                    if data == "":
                        break
                    size += 1
                    self._last_healthcheck = HealthCheckStatus.deserialize(data)
                    if self._last_healthcheck[2] == "green":
                        self._last_greenhealthcheck = self._last_healthcheck
                        self._last_greenyellowhealthcheck = self._last_healthcheck
                    elif self._last_healthcheck[2] == "yellow":
                        self._last_yellowhealthcheck = self._last_healthcheck
                        self._last_greenyellowhealthcheck = self._last_healthcheck
                    elif self._last_healthcheck[2] == "red":
                        self._last_redhealthcheck = self._last_healthcheck
                    else:
                        self._last_errorhealthcheck = self._last_healthcheck
            self._size = size
        except FileNotFoundError as ex:
            self._size = 0
            self._last_healthcheck = None
            folder = os.path.dirname(self._filepath)
            await utils.makedir(folder)

    async def save(self,healthcheckstatus):
        """
        Return True if write; Return False if the page is already full and can't write anymore.

        """
        if self._size is None:
            await self._load()
        if self._size >= settings.HEALTHSTATUS_PAGESIZE:
            return False
        data = HealthCheckStatus.serialize(healthcheckstatus)
        async with aiofiles.open(self._filepath,'ab') as f:
            if self._size > 0:
                await f.write(b"\n" + data.encode())
            else:
                await f.write(data.encode())
            self._size += 1

        self._last_healthcheck = healthcheckstatus

        return True

    async def pageitems(self):
        """
        used by history page 
        Ignore the corrupted data
        Return a generator for healthcheck items in this page

        """
        if self._size is None:
            await self._load()
        if self._size  == 0:
            return
        async with aiofiles.open(self._filepath,'r') as f:
            while True:
                data = await f.readline()
                if data == "":
                    break
                else:
                    try:
                        yield HealthCheckStatus.deserialize(data)
                    except Exception as ex:
                        logger.error("The data({0}) in file({0}) is corrupted.".format(self._filepath,data))
                        continue

    async def reversed_pageitems(self):
        data = [d for d in await self.pageitems()]
        return reversed(data)

class LastHealthCheck(HealthCheckPage):
    def __init__(self,healthcheckpages,path):
        super().__init__(healthcheckpages,None,path)

    def serialize(self):
        raise Exception("Not Support")

    @classmethod
    def deserialize(cls,data):
        raise Exception("Not Support")

    async def save(self,healthcheckstatus):
        """
        Return True if write; Return False if the page is already full and can't write anymore.

        """
        if self._size is None:
            await self._load()
        data = HealthCheckStatus.serialize(healthcheckstatus)
        async with aiofiles.open(self._filepath,'wb') as f:
            await f.write(data.encode())
        self._size += 1

        self._last_healthcheck = healthcheckstatus

        return True

class LastHealthCheckInMemory(LastHealthCheck):

    def __init__(self,healthcheckpages,filepath):
        super().__init__(healthcheckpages,filepath)
        self._size = 0

    async def get_detailfile(self,starttime):
        return ""

    async def get_last_healthcheck(self):
        return self._last_healthcheck
 
    async def get_last_greenhealthcheck(self):
        return self._last_greenhealthcheck

    async def get_last_yellowhealthcheck(self):
        return self._last_yellowhealthcheck

    async def get_last_greenyellowhealthcheck(self):
        return self._last_greenyellowhealthcheck

    async def get_last_redhealthcheck(self):
        return self._last_redhealthcheck

    async def get_last_errorhealthcheck(self):
        return self._last_errorhealthcheck

    def save(self,healthcheckstatus):
        """
        The data is in memeory, no need to save
        Return True if write; Return False if the page is already full and can't write anymore.
        """
        self._size = 1

        self._last_healthcheck = healthcheckstatus

        return True

    def _load(self):
        """
        Never persistent the data to file system
        no need to load the data

        """
        self._size = 0

class BasicHealthCheckPages(object):
    """
    pages: a list of page data([startdatetime,page file])
    """
    _instances = None
    _pageindexfilename = None
    _pagefilename = None
    _pagefolder = None
    def __init__(self,servicehealthcheck):
        self._servicehealthcheck = servicehealthcheck
        self._pages = None #from earlist to latest
        self._filesize = None
        self.next_management_time = None
        self._historyexpire = 0
        self.historyenabled = False


    @classmethod
    def get_instance(cls,servicehealthcheck):
        configdata = cls._instances.get(servicehealthcheck.healthcheck.configfile)
        if not configdata:
            configdata = {}
            cls._instances[servicehealthcheck.healthcheck.configfile] = configdata

        sectiondata = configdata.get(servicehealthcheck.sectionid)
        if not sectiondata:
            obj = cls(servicehealthcheck)
            cls._instances[servicehealthcheck.sectionid] = {servicehealthcheck.id:obj}
        else:
            obj = sectiondata.get(servicehealthcheck.id)
            if not obj:
                obj = cls(servicehealthcheck)
                sectiondata[servicehealthcheck.id] = obj
            else:
                obj.servicehealthcheck = servicehealthcheck

        return obj

    @property
    def servicehealthcheck(self):
        return self._servicehealthcheck

    @servicehealthcheck.setter
    def servicehealthcheck(self,servicehealthcheck):
        if self.historyenabled != servicehealthcheck.historyenabled:
            self._pages = None
        self._servicehealthcheck = servicehealthcheck
        self.historyenabled = self._servicehealthcheck.historyenabled

    _basedir = None
    @property
    def basedir(self):
        if not self._basedir:
            folder = os.path.join(settings.HEALTHCHECK_DATA_DIR,os.path.splitext(os.path.basename(self._servicehealthcheck.healthcheck.configfile))[0],self._servicehealthcheck.sectionid,self._servicehealthcheck.id)
            self._basedir = folder

        return self._basedir

    _pageindexfile = None
    @property
    def pageindexfile(self):
        if not self.historyenabled:
            raise Exception("{}: History health check is disabled".format(self._servicehealthcheck))

        if self._pageindexfile is None:
            self._pageindexfile = os.path.join(self.basedir,self._pageindexfilename)

        return self._pageindexfile

    def pagedir(self,starttime):
        return os.path.join(self.basedir,self._pagefolder)

    def pagefile(self,starttime):
        return os.path.join(self.pagedir(starttime),self._pagefilename.format(starttime.strftime("%Y%m%dT%H%M%S")))

    @property
    def detailsdir(self):
        return  os.path.join(self.basedir,"details")

    _previous_detail_dir = None
    async def get_detaildir(self,starttime):
        folder =  os.path.join(self.detailsdir,starttime.strftime("%Y%m%d"))
        if self._previous_detail_dir != folder:
            await utils.makedir(folder)
            self._previous_detail_dir = folder
        return folder

    async def get_detailfile(self,starttime):
        return os.path.join(await self.get_detaildir(starttime),"{}.json".format(starttime.strftime("%Y%m%dT%H%M%S")))

    async def get_last_healthcheck(self):
        """
        Called by healthcheck server
        because _pages are loaded and catched in memory and only healthcheck server can change this file, no need to check whether the file was changed by other process after loading.
        """
        if self._pages is None:
            await self._load()
        if self._pages:
            return await self._pages[-1].get_last_healthcheck()
        else:
            return None

    async def _load(self):
        if self._pages is not None:
            #already loaded
            return

        pages = []
        try:
            async with aiofiles.open(self.pageindexfile,'r') as f:
                while True:
                    data = await f.readline()
                    if data == "":
                        break
                    data = data.strip()
                    if not data:
                        continue
                    try:
                        pages.append(HealthCheckPage.deserialize(self,data))
                    except Exception as ex:
                        logger.error("The page data({1}) in file({0}) is corrupted".format(self.pageindexfile,data))
            self._filesize = await aiofiles.os.path.getsize(self.pageindexfile)
        except FileNotFoundError as ex:
            folder = os.path.dirname(self.pageindexfile)
            await utils.makedir(folder)
            self._filesize = 0

        self._pages = pages

    def reset(self):
        """
        Reset the pages to reload it
        """
        self._filesize = None
        self._pages = None

    async def get_pages(self):
        """
        Called by web app; should reload if if it was changed by healthcheck server
        """
        if self._pages is None or self._filesize is None:
            await self._load()
        else:
            try:
                filesize = await aiofiles.os.path.getsize(self.pageindexfile)
            except FileNotFoundError as ex:
                filesize = 0
            if self._filesize != filesize:
                await self._load()
        return self._pages

    async def save_healthcheckstatus(self,healthcheckstatus):
        """
        Return True if expired pages have been cleaned; otherwise return False
        """
        if self._pages is None:
            await self._load()

        try:
            cleaned = False
            if self._pages:
                if isinstance(self._pages[-1],LastHealthCheckInMemory):
                    self._pages[-1].save(healthcheckstatus)
                    return False
                elif await self._pages[-1].save(healthcheckstatus):
                    #already saved to the last pages, new new page is added, no need to manage the healthcheck history
                    return False

            #the last page is full, add a new page 
            newpage = HealthCheckPage(self,healthcheckstatus[0],self.pagefile(healthcheckstatus[0]))

            async with aiofiles.open(self.pageindexfile,'ab') as f:
                if self._pages:
                    await f.write(b"\n" + newpage.serialize().encode())
                else:
                    await f.write(newpage.serialize().encode())
            self._pages.append(newpage)

            await newpage.save(healthcheckstatus)
        except FileNotFoundError as ex:
            #file doesn't exist. maybe delete from disk directly.
            #reload the pages and do it again
            self.reset()
            await self.save_healthcheckstatus(healthcheckstatus)
        finally:
            cleaned = await self.managepages()

        return cleaned


    async def managepages(self):
        """
        Return True if expired pages have been cleaned; otherwise return False
        """
        if not self.historyenabled:
            #no need to manage
            return  False

        now = utils.now()
        if self.next_management_time and now < self.next_management_time:
            manage_history = False
        else :
            manage_history = True
        if self.next_management_time:
            self.next_management_time += timedelta(days=1)
        else:
            self.next_management_time = datetime(now.year,now.month,now.day,tzinfo=settings.TZ) + timedelta(days=1)

        if manage_history:
            ealiest_nonexpiretime = datetime(now.year,now.month,now.day,tzinfo=settings.TZ)
            if self._historyexpire > 1:
                ealiest_nonexpiretime -= timedelta(days=self._historyexpire - 1)
            #find the index of the last expired data
            index_of_latest_expiredata = -1
            for i in range(len(self._pages)):
                if self._pages[i].starttime < ealiest_nonexpiretime:
                    #this page contains some expire data, but also can contain non-expired data.
                    continue
                else:
                    #This is the first page whose data is not expired.
                    #The previous page must contain expired data, and maybe contain non-expired data
                    #Preserve the previous page too
                    index_of_latest_expiredata = i - 2
                    break

            if index_of_latest_expiredata >= 0:
                #remove expired data from memory
                for i in range(index_of_latest_expiredata,-1,-1):
                    try:
                        await self._pages[i].delete()
                        del self._pages[i]
                    except Exception as ex:
                        logger.error("Failed to remove the page '{}'. {} : {}".format(self._pages[i].filepath,ex.__class__.__name__,str(ex)))
                #save to file
                async with aiofiles.open(self.pageindexfile,'wb') as f:
                    for i in range(len(self._pages)):
                        if i > 0:
                            await f.write(b"\n" + self._pages[i].serialize().encode())
                        else:
                            await f.write(self._pages[i].serialize().encode())
            return True
        else:
            return False

class HealthCheckPages(BasicHealthCheckPages):
    """
    pages: a list of page data([startdatetime,page file])
    """
    _instances = {}
    _pageindexfilename = "pageindex.json"
    _pagefilename = "page_{}.json"
    _pagefolder = "pages"
    def __init__(self,servicehealthcheck):
        super().__init__(servicehealthcheck)
        self.historyenabled = self._servicehealthcheck.historyenabled
        self._lock = FileLock(os.path.join(self.basedir,".lock"))
        self._historyexpire = self._servicehealthcheck.historyexpire
        self._errorpages = HealthCheckErrorPages.get_instance(servicehealthcheck) if servicehealthcheck.errorhistoryenabled else None

    @property
    def errorpages(self):
        return self._errorpages

    async def get_last_greenhealthcheck(self):
        """
        Called by healthcheck server
        because _pages are loaded and catched in memory and only healthcheck server can change this file, no need to check whether the file was changed by other process after loading.
        """
        if self._pages is None:
            await self._load()
        if self._pages:
            for i in range(len(self._pages) - 1,-1,-1):
                data = await self._pages[i].get_last_greenhealthcheck()
                if data:
                    return data
            return None
        else:
            return None

    async def get_last_yellowhealthcheck(self):
        """
        Called by healthcheck server
        because _pages are loaded and catched in memory and only healthcheck server can change this file, no need to check whether the file was changed by other process after loading.
        """
        if self._pages is None:
            await self._load()
        if self._pages:
            for i in range(len(self._pages) - 1,-1,-1):
                data = await self._pages[i].get_last_yellowhealthcheck()
                if data:
                    return data
            return None
        else:
            return None

    async def get_last_greenyellowhealthcheck(self):
        """
        Called by healthcheck server
        because _pages are loaded and catched in memory and only healthcheck server can change this file, no need to check whether the file was changed by other process after loading.
        Return the last green or yellow healthcheck whichever is the latest one.
        """
        if self._pages is None:
            await self._load()
        if self._pages:
            for i in range(len(self._pages) - 1,-1,-1):
                data = await self._pages[i].get_last_greenyellowhealthcheck()
                if data:
                    return data

            return None
        else:
            return None

    async def get_last_redhealthcheck(self):
        """
        Called by healthcheck server
        because _pages are loaded and catched in memory and only healthcheck server can change this file, no need to check whether the file was changed by other process after loading.
        """
        if self._pages is None:
            await self._load()
        if self._pages:
            for i in range(len(self._pages) - 1,-1,-1):
                data = await self._pages[i].get_last_redhealthcheck()
                if data:
                    return data
            return None
        else:
            return None

    async def get_last_errorhealthcheck(self):
        """
        Called by healthcheck server
        because _pages are loaded and catched in memory and only healthcheck server can change this file, no need to check whether the file was changed by other process after loading.
        """
        if self._pages is None:
            await self._load()
        if self._pages:
            for i in range(len(self._pages) - 1,-1,-1):
                data = await self._pages[i].get_last_errorhealthcheck()
                if data:
                    return data
            return None
        else:
            return None

    def pagefile(self,starttime):
        if self.historyenabled:
            return super().pagefile(starttime)
        else:
            return os.path.join(self.basedir,"latesthealthcheck.json")

    async def get_detailfile(self,starttime):
        if not self._servicehealthcheck.url:
            #is not a real healthcheck,for example: heartbeat
            raise Exception("Not Support")
        elif not self.historyenabled:
            return os.path.join(self.basedir,"lasthealthcheckdetails.json")
        else:
            return await super().get_detailfile(starttime)
        
        return os.path.join(self.basedir,"{}.json".format(starttime.strftime("%Y%m%dT%H%M%S")))

    async def _load(self):
        if not self._servicehealthcheck.url:
            #is not a real healthcheck,for example: heartbeat
            self._pages = [LastHealthCheckInMemory(self,self.pagefile(None))]
        elif not self.historyenabled:
            self._pages = [LastHealthCheck(self,self.pagefile(None))]
        else:
            await super()._load()

    async def get_pages(self):
        """
        Called by web app; should reload if if it was changed by healthcheck server
        """
        if not self._servicehealthcheck.url:
            return self._pages 
        elif not self.historyenabled:
            return self._pages 
        else:
            return await super().get_pages()

    async def save(self,healthcheckstatus,details=None):
        cleaned = False
        async with self._lock:
            try:
                cleaned = await super().save_healthcheckstatus(healthcheckstatus)
            finally:
                if details:
                    async with aiofiles.open(await self._pages[-1].get_detailfile(healthcheckstatus[0]),'w') as f:
                        await f.write(json.dumps(details,cls=serializers.JSONFormater))

            if self._errorpages and healthcheckstatus[2] != "green" and healthcheckstatus[2] in self._servicehealthcheck.healthdetailpersistent:
                cleaned = cleaned or await self._errorpages.save_healthcheckstatus(healthcheckstatus)
            
            if cleaned:
                #a history clean action was performed. try to clean the expired details
                #Find the earliest starttime of the details
                starttime = None
                if self._historyexpire > 0:
                    starttime = self._pages[0]._starttime
                if self._errorpages._historyexpire > 0 and self._errorpages._pages:
                    if not starttime:
                        starttime = self._errorpages._pages[0]._starttime
                    elif starttime > self._errorpages._pages[0]._starttime:
                        starttime = self._errorpages._pages[0]._starttime
                if starttime:
                    #find the start time, clean all the details before that day
                    ealiest_nonexpiretime = starttime.strftime("%Y-%m-%d")
                    if await aiofiles.os.path.exists(self.detailsdir):
                        for d in await aiofiles.os.listdir(self.detailsdir):
                            if d < ealiest_nonexpiretime:
                                await utils.deletedir(os.path.join(self.detailsdir,d))

class HealthCheckErrorPages(BasicHealthCheckPages):
    """
    pages: a list of page data([startdatetime,page file])
    """
    _instances = {}
    _pageindexfilename = "errorpageindex.json"
    _pagefilename = "errorpage_{}.json"
    _pagefolder = "errorpages"
    def __init__(self,servicehealthcheck):
        super().__init__(servicehealthcheck)
        self.historyenabled = self._servicehealthcheck.errorhistoryenabled
        self._historyexpire = self._servicehealthcheck.errorhistoryexpire

