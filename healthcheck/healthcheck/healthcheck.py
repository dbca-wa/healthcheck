import hashlib
import shutil
import json
import os
import aiofiles.os
import logging

from .. import settings
from .. import utils

from .base import BaseHealthCheck,JsonStatusMixin,PRTGMixin
from .healthcheckedit import EditingHealthCheck

logger = logging.getLogger(__name__)

class SystemViewMeta(list):
    """
    A list type of meta data for system view
    """
    @property
    def id(self):
        return self[0]

    @id.setter
    def id(self,v):
        self[0] = v


    @property
    def title(self):
        return self[1]

    @title.setter
    def title(self,v):
        self[1] = v

    @property
    def description(self):
        return self[2]

    @description.setter
    def description(self,v):
        self[2] = v

class PRTGSensorMeta(SystemViewMeta):
    """
    A list type of meta data for system view
    """
    pass

class UserViewMeta(object):
    """
    The meta data for user view
    """
    def __init__(self,user,healthcheck):
        self._user = user
        self._healthcheck = healthcheck

    def __str__(self):
        return self._user

    @property
    def title(self):
        return "Systems Health Check -- {}".format(self._user)

    @property
    def description(self):
        return "The view contains only user interested systems"

class SelectablePRTGChannel(object):
    """
    Represent a selectable prtg channel of a service to choose by user in web page.
    """
    def __init__(self,channelid,channelname,selected):
        self.channelid = channelid
        self.channelname = channelname
        self.selected = selected


class ServiceHealthCheckPRTGSensor(object):
    def __init__(self,servicehealthcheck,channelsettings):
        self._servicehealthcheck = servicehealthcheck
        self._channelsettings = channelsettings

    def __getattr__(self,name):
        return getattr(self._servicehealthcheck,name)

    def __getitem__(self,name):
        return self._servicehealthcheck.get(name)

    @property
    def prtgchannels(self):
        if not self._channelsettings:
            return []
        return filter(lambda item: item[0] in self._channelsettings, self._servicehealthcheck.prtgchannels)

    @property
    def selectableprtgchannels(self):
        return map(lambda item: SelectablePRTGChannel(item[0],item[1][0]["channel"],self._channelsettings is not None and item[0] in self._channelsettings),self._servicehealthcheck.prtgchannels)

class SelectableServiceHealthCheck(object):
    """
    Represent a selectable healthcheck service  in section to choose by user in web page.
    """
    def __init__(self,servicehealthcheck,selected):
        self._servicehealthcheck = servicehealthcheck
        self.selected = selected

    def __getattr__(self,name):
        return getattr(self._servicehealthcheck,name)

    def __getitem__(self,name):
        return self._servicehealthcheck.get(name)


class SectionHealthCheckView(object):
    def __init__(self,sectionhealthcheck,viewsettings):
        self._sectionhealthcheck = sectionhealthcheck
        self._viewsettings = viewsettings

    def __getattr__(self,name):
        return getattr(self._sectionhealthcheck,name)

    def __getitem__(self,name):
        return self._sectionhealthcheck.get(name)

    @property
    def healthcheckservices(self):
        if self._viewsettings is not None and not self._viewsettings:
            return []
        return filter(lambda service: self._viewsettings is None or service.id in self._viewsettings, self._sectionhealthcheck.healthcheckservices)

    @property
    def selectablehealthcheckservices(self):
        return map(lambda service: SelectableServiceHealthCheck(service,self._viewsettings is not None and service.id in self._viewsettings),self._sectionhealthcheck.healthcheckservices)

class SectionHealthCheckPRTGSensor(object):
    def __init__(self,sectionhealthcheck,sensorsettings):
        self._sectionhealthcheck = sectionhealthcheck
        self._sensorsettings = sensorsettings

    def __getattr__(self,name):
        return getattr(self._sectionhealthcheck,name)

    def __getitem__(self,name):
        return self._sectionhealthcheck.get(name)

    @property
    def healthcheckservices(self):
        if not self._sensorsettings:
            return []
        return map(lambda service: ServiceHealthCheckPRTGSensor(service,self._sensorsettings.get(service.id,None) ), filter(lambda service: service.prtgenabled and service.id in self._sensorsettings, self._sectionhealthcheck.healthcheckservices))

    @property
    def selectablehealthcheckservices(self):
        return map(lambda service: ServiceHealthCheckPRTGSensor(service,self._sensorsettings.get(service.id,None) if self._sensorsettings else None ), filter(lambda service:service.prtgenabled,self._sectionhealthcheck.healthcheckservices))


class HealthCheckView(PRTGMixin,JsonStatusMixin):
    """
    Represent a healthcheck dashboard view
    """
    def __init__(self,healthcheck,viewmeta,viewsettings):
        self._healthcheck = healthcheck
        self._viewmeta = viewmeta
        self._viewsettings = viewsettings

    @property
    def title(self):
        return self._viewmeta.title if self._viewmeta else ""

    @property
    def healthchecksections(self):
        if not self._viewsettings:
            return self._healthcheck.healthchecksections
        return map(lambda section: SectionHealthCheckView(section,self._viewsettings.get(section.id,set()) if self._viewsettings else None ), filter(lambda section: section.id in self._viewsettings, self._healthcheck.healthchecksections))

    @property
    def selectablehealthchecksections(self):
        return map(lambda section: SectionHealthCheckView(section,self._viewsettings.get(section.id,set()) if self._viewsettings else None ), self._healthcheck.healthchecksections)

class HealthCheckPRTGSensor(PRTGMixin,JsonStatusMixin):
    """
    Represent a healthcheck PRTG sensor
    """
    def __init__(self,healthcheck,sensormeta,sensorsettings):
        self._healthcheck = healthcheck
        self._sensormeta = sensormeta
        self._sensorsettings = sensorsettings

    @property
    def title(self):
        return self._sensormeta.title if self._sensormeta else ""

    @property
    def healthchecksections(self):
        if not self._sensorsettings:
            return []
        return map(lambda section: SectionHealthCheckPRTGSensor(section,self._sensorsettings.get(section.id,{}) ), filter(lambda section: section.prtgenabled and self._sensorsettings and section.id in self._sensorsettings, self._healthcheck.healthchecksections))

    @property
    def selectablehealthchecksections(self):
        return map(lambda section: SectionHealthCheckPRTGSensor(section,self._sensorsettings.get(section.id,{}) if self._sensorsettings else None ), filter(lambda section: section.prtgenabled,self._healthcheck.healthchecksections))

class HealthCheck(BaseHealthCheck):
    INSTANCE = None

    def __init__(self,configfile=settings.HEALTHCHECK_CONFIGFILE):
        super().__init__(configfile=configfile)
        self._views = {}
        self._prtgsensorsconfig = {}

    @classmethod
    async def create_instance(cls,configfile=settings.HEALTHCHECK_CONFIGFILE):
        instance = cls(configfile=configfile)
        await instance.load_configs()

        configdir,configfilename = os.path.split(instance.configfile)
        configfilebase,configfileext = os.path.splitext(configfilename)

        instance._publishhistorydir = os.path.join(configdir,"{}.publishhistories".format(configfilebase))
        await utils.makedir(instance.publishhistorydir)

        instance._publishhistoryfile = os.path.join(instance.publishhistorydir,"publishhistories.json")
        if not await aiofiles.os.path.exists(instance.publishhistoryfile):
            configfilename = os.path.basename(instance.configfile)
            configfilebase,configfileext = os.path.splitext(configfilename)
            initialfile = os.path.join(instance.publishhistorydir,"{}.initial{}".format(configfilebase,configfileext))
            if instance.sections:
                await utils.copyfile(instance.configfile,initialfile)
                async with aiofiles.open(instance.publishhistoryfile,'w') as f:
                    await f.write(json.dumps(["Initial",os.path.basename(initialfile),"","Initial"]))
            else:
                async with aiofiles.open(tmpfile,'w') as f:
                    pass
        elif not await aiofiles.os.path.isfile(instance.publishhistoryfile):
            raise Exception("The publish history file({}) is not a file".format(instance.publishhistoryfile))

        instance._systemviewsdir = os.path.join(configdir,"{}.views".format(configfilebase),"system")
        if not await aiofiles.os.path.exists(instance.systemviewsdir):
            await utils.makedir(instance.systemviewsdir)
        elif not await aiofiles.os.path.isdir(instance.systemviewsdir):
            raise Exception("The path({}) is not a folder".format(instance.systemviewsdir))

        instance._viewsfile = os.path.join(instance.systemviewsdir,"systemviews.json")

        instance._prtgsensorsdir = os.path.join(configdir,"{}.prtgsensors".format(configfilebase))
        if not await aiofiles.os.path.exists(instance.prtgsensorsdir):
            await utils.makedir(instance.prtgsensorsdir)
        elif not await aiofiles.os.path.isdir(instance.prtgsensorsdir):
            raise Exception("The path({}) is not a folder".format(instance.prtgsensorsdir))

        instance._prtgsensorsfile = os.path.join(instance.prtgsensorsdir,"prtgsensors.json")

        instance._userviewsdir = os.path.join(configdir,"{}.views".format(configfilebase),"user")
        if not await aiofiles.os.path.exists(instance.userviewsdir):
            await utils.makedir(instance.userviewsdir)
        elif not await aiofiles.os.path.isdir(instance.userviewsdir):
            raise Exception("The path({}) is not a folder".format(instance.userviewsdir))

        return instance

    @classmethod
    async def get_instance(cls):
        if not cls.INSTANCE:
            cls.INSTANCE = await cls.create_instance()

        return cls.INSTANCE

    _editconfigdir = None
    @property
    def editconfigdir(self):
        if not self._editconfigdir:
            configdir,configfilename = os.path.split(self.configfile)
            self._editconfigdir = configdir
        return self._editconfigdir

    _editconfigfile = None
    async def get_editconfigfile(self):
        if not self._editconfigfile:
            configfilename = os.path.basename(self.configfile)
            configfilebase,configfileext = os.path.splitext(configfilename)
            f = os.path.join(self.editconfigdir,"{}.edit{}".format(configfilebase,configfileext))
            self._editconfigfile = f

        if not await aiofiles.os.path.exists(self._editconfigfile):
            if await aiofiles.os.path.exists(self.configfile):
                await utils.copyfile(self.configfile,self._editconfigfile)
            else:
                async with aiofiles.open(self._editconfigfile,'w') as fw:
                    await fw.write("[]")
        elif not await aiofiles.os.path.isfile(self._editconfigfile):
            raise Exception("The editing config file({}) is not a file".format(self._editconfigfile))

        return self._editconfigfile

    _publishhistorydir = None
    @property
    def publishhistorydir(self):
        return self._publishhistorydir

    _publishhistoryfile = None
    @property
    def publishhistoryfile(self):
        return self._publishhistoryfile

    _editing_healthcheck = None
    async def get_editing_healthcheck(self):
        if not self._editing_healthcheck:
            self._editing_healthcheck = await EditingHealthCheck.create_instance(self,await self.get_editconfigfile())

        return self._editing_healthcheck

    async def get_publishhistories(self):
        histories = []
        async with aiofiles.open(self.publishhistoryfile,'r') as fr:
            while True:
                line = await fr.readline()
                if not line:
                    break
                else:
                    try:
                        histories.append(json.loads(line.strip()))
                    except Exception as ex:
                        logger.error("{}: Failed to parse the published healthcheck configuration data ({})".format(self,line))

        return histories

    async def rollback(self,configfile):
        configfile = os.path.join(self.publishhistorydir,configfile)

        try:
            async with aiofiles.open(configfile,'rb') as f:
                configtxt = await f.read()
        except FileNotFoundError as ex:
            raise FileNotFoundError("{}: The config file({}) doesn't exist".format(self,configfile))

        config_hashcode = hashlib.sha256(configtxt).hexdigest()
        if config_hashcode == self.config_hashcode:
            logger.debug("{}: config file is not changed".format(self))
            return False

        await utils.copyfile(configfile,self.configfile)
        return True

    @property 
    def systemviewsdir(self):
        return self._systemviewsdir


    @property 
    def prtgsensorsdir(self):
        return self._prtgsensorsdir

    @property 
    def userviewsdir(self):
        return self._userviewsdir

    def get_viewdir(self,key):
        if "@" in key:
            #key is a user
            return self.userviewsdir
        else:
            #key is a system
            return self.systemviewsdir

    @property
    def systemviewsfile(self):
        return self._viewsfile

    @property
    def prtgsensorsfile(self):
        return self._prtgsensorsfile

    _systemviews = [None,None,[]]
    async def get_systemviews(self):
        if not await aiofiles.os.path.exists(self.systemviewsfile):
            self._systemviews[0] = None
            self._systemviews[1] = None
            self._systemviews[2].clear()
            return self._systemviews[2]

        file_size = await aiofiles.os.path.getsize(self.systemviewsfile)
        if not file_size:
            await utils.removefile(self.systemviewsfile)
            self._systemviews[0] = None
            self._systemviews[1] = None
            self._systemviews[2].clear()
            return self._systemviews[2]

        file_mtime = await aiofiles.os.path.getmtime(self.systemviewsfile)
        if self._systemviews[0] != file_mtime  or self._systemviews[1] != file_size:
            systemviews = []
            async with aiofiles.open(self.systemviewsfile,'r') as f:
                while True:
                    line = await f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        systemviews.append(SystemViewMeta(json.loads(line)))
                    except Exception as ex :
                        logger.error("{}: Failed to parse the system view({})".format(self,line))

            if not systemviews:
                #no system views
                await utils.removefile(self.systemviewsfile)
                self._systemviews = [None,None,None]
                return []
            self._systemviews[0] = file_mtime
            self._systemviews[1] = file_size
            self._systemviews[2] = systemviews

        return self._systemviews[2]

    _prtgsensors = [None,None,[]]
    async def get_prtgsensors(self):
        if not await aiofiles.os.path.exists(self.prtgsensorsfile):
            self._prtgsensors[0] = None
            self._prtgsensors[1] = None
            self._prtgsensors[2].clear()
            return self._prtgsensors[2]

        file_size = await aiofiles.os.path.getsize(self.prtgsensorsfile)
        if not file_size:
            await utils.removefile(self.prtgsensorsfile)
            self._prtgsensors[0] = None
            self._prtgsensors[1] = None
            self._prtgsensors[2].clear()
            return self._prtgsensors[2]

        file_mtime = await aiofiles.os.path.getmtime(self.prtgsensorsfile)
        if self._prtgsensors[0] != file_mtime  or self._prtgsensors[1] != file_size:
            prtgsensors = []
            async with aiofiles.open(self.prtgsensorsfile,'r') as f:
                while True:
                    line = await f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        prtgsensors.append(PRTGSensorMeta(json.loads(line)))
                    except Exception as ex :
                        logger.error("{}: Failed to parse the system view({})".format(self,line))

            if not prtgsensors:
                #no system views
                await utils.removefile(self.prtgsensorsfile)
                self._prtgsensors = [None,None,None]
                return []
            self._prtgsensors[0] = file_mtime
            self._prtgsensors[1] = file_size
            self._prtgsensors[2] = prtgsensors

        return self._prtgsensors[2]

    async def get_viewsettings(self,key):
        """
        Return None if no customization, otherwise return {section:service set}
        """
        if not key:
            return None
        viewfile = os.path.join(self.get_viewdir(key),"{}.json".format(key))

        if not await aiofiles.os.path.exists(viewfile):
            if key in self._views:
                del self._views[key]
            return None
        
        file_size = await aiofiles.os.path.getsize(viewfile)
        file_mtime = await aiofiles.os.path.getmtime(viewfile)
        if not file_size:
            #no customization
            await utils.removefile(viewfile)
            if key in self._views:
                del self._views[key]
            return None

        if key not in self._views or self._views[key][0] != file_mtime or self._views[key][1] != file_size:
            #file changed, reload the file
            async with aiofiles.open(viewfile) as f:
                data = await f.read()
            viewsettings = json.loads(data)
            #turn the service list to service set
            #remove the empty secion
            for sector in [k for k in viewsettings.keys()]:
                viewsettings[sector] = set(viewsettings[sector])
                if not viewsettings[sector]:
                    del viewsettings[sector]
            if viewsettings:
                self._views[key] = [file_mtime,file_size,viewsettings]
            else:
                #no customization
                await utils.removefile(viewfile)
                if key in self._views:
                    del self._views[key]
                return None


        return self._views[key][2]

    async def get_prtgsensorsettings(self,key):
        """
        Return None if no customization, otherwise return {section:{service: sensor set}}
        """
        if not key:
            return None
        sensorfile = os.path.join(self.prtgsensorsdir,"{}.json".format(key))

        if not await aiofiles.os.path.exists(sensorfile):
            if key in self._prtgsensorsconfig:
                del self._prtgsensorsconfig[key]
            return None
        
        file_size = await aiofiles.os.path.getsize(sensorfile)
        file_mtime = await aiofiles.os.path.getmtime(sensorfile)
        if not file_size:
            #no customization
            await utils.removefile(sensorfile)
            if key in self._prtgsensorsconfig:
                del self._prtgsensorsconfig[key]
            return None

        if key not in self._prtgsensorsconfig or self._prtgsensorsconfig[key][0] != file_mtime or self._prtgsensorsconfig[key][1] != file_size:
            #file changed, reload the file
            async with aiofiles.open(sensorfile) as f:
                data = await f.read()
            sensorsettings = json.loads(data)
            #turn the service list to service set
            #remove the empty secion
            for sector in [k1 for k1 in sensorsettings.keys()]:
                sectorsettings = sensorsettings[sector]
                for service in [k2 for k2 in sectorsettings.keys()]:
                    sectorsettings[service] = set(sectorsettings[service])
                    if not sectorsettings[service]:
                        del sectorsettings[service]
                if not sectorsettings:
                    del sensorsettings[sector]
            if sensorsettings:
                self._prtgsensorsconfig[key] = [file_mtime,file_size,sensorsettings]
            else:
                #no customization
                await utils.removefile(sensorfile)
                if key in self._prtgsensorsconfig:
                    del self._prtgsensorsconfig[key]
                return None

        return self._prtgsensorsconfig[key][2]

    async def save_systemview(self,viewid,title,description):
        systemviews = await self.get_systemviews()
        systemview = next((v for v in systemviews if v.id == viewid),None)
        if systemview:
            if systemview.title != title or systemview.description != description:
                #changed
                systemview.title = title
                systemview.description = description
                firstline = True
                async with aiofiles.open(self.systemviewsfile,'wb') as f:
                    for view in systemviews:
                        if not firstline:
                            await f.write(b'\n' + json.dumps(view).encode())
                        else:
                            firstline = False
                            await f.write(json.dumps(view).encode())

                
                self._systemviews[0] = await aiofiles.os.path.getmtime(self.systemviewsfile)
                self._systemviews[1] = await aiofiles.os.path.getsize(self.systemviewsfile)
                logger.debug("{}: Update the system views file({})".format(self,self.systemviewsfile))
        else:
            systemview = SystemViewMeta([viewid,title,description])
            async with aiofiles.open(self.systemviewsfile,'ab') as f:
                if len(systemviews) > 0:
                    await f.write(b'\n' + json.dumps(systemview).encode())
                else:
                    await f.write(json.dumps(systemview).encode())

            systemviews.append(systemview)
            self._systemviews[0] = await aiofiles.os.path.getmtime(self.systemviewsfile)
            self._systemviews[1] = await aiofiles.os.path.getsize(self.systemviewsfile)
            logger.debug("{}: Append the system views file({})".format(self,self.systemviewsfile))

    async def save_prtgsensor(self,sensorid,title,description):
        prtgsensors = await self.get_prtgsensors()
        sensor = next((v for v in prtgsensors if v.id == sensorid),None)
        if sensor:
            if sensor.title != title or sensor.description != description:
                #changed
                sensor.title = title
                sensor.description = description
                firstline = True
                async with aiofiles.open(self.prtgsensorsfile,'wb') as f:
                    for view in prtgsensors:
                        if not firstline:
                            await f.write(b'\n' + json.dumps(view).encode())
                        else:
                            firstline = False
                            await f.write(json.dumps(view).encode())

                
                self._prtgsensors[0] = await aiofiles.os.path.getmtime(self.prtgsensorsfile)
                self._prtgsensors[1] = await aiofiles.os.path.getsize(self.prtgsensorsfile)
                logger.debug("{}: Update the prtg sensor file({})".format(self,self.prtgsensorsfile))
        else:
            sensor = PRTGSensorMeta([sensorid,title,description])
            async with aiofiles.open(self.prtgsensorsfile,'ab') as f:
                if len(prtgsensors) > 0:
                    await f.write(b'\n' + json.dumps(sensor).encode())
                else:
                    await f.write(json.dumps(sensor).encode())

            prtgsensors.append(sensor)
            self._prtgsensors[0] = await aiofiles.os.path.getmtime(self.prtgsensorsfile)
            self._prtgsensors[1] = await aiofiles.os.path.getsize(self.prtgsensorsfile)
            logger.debug("{}: Append the prtg sensor file({})".format(self,self.prtgsensorsfile))

    async def delete_systemview(self,viewid):
        systemviews = await self.get_systemviews()
        pos = next((i for i in range(len(systemviews)) if systemviews[i].id == viewid),-1)
        if pos == -1:
            return
        systemview = systemviews[pos]
        #remove system view settings in memory and delte setting file
        await self.save_viewsettings(systemview.id)

        #delete from systemviews
        del systemviews[pos]
        firstline = True
        async with aiofiles.open(self.systemviewsfile,'wb') as f:
            for view in systemviews:
                if not firstline:
                    await f.write(b'\n' + json.dumps(view).encode())
                else:
                    firstline = False
                    await f.write(json.dumps(view).encode())
        self._systemviews[0] = await aiofiles.os.path.getmtime(self.systemviewsfile)
        self._systemviews[1] = await aiofiles.os.path.getsize(self.systemviewsfile)

    async def delete_prtgsensor(self,sensorid):
        prtgsensors = await self.get_prtgsensors()
        pos = next((i for i in range(len(prtgsensors)) if prtgsensors[i].id == sensorid),-1)
        if pos == -1:
            return
        sensor = prtgsensors[pos]
        #remove system view settings in memory and delte setting file
        await self.save_prtgsensorsettings(sensor.id)

        #delete from prtgsensors
        del prtgsensors[pos]
        firstline = True
        async with aiofiles.open(self.prtgsensorsfile,'wb') as f:
            for sensor in prtgsensors:
                if not firstline:
                    await f.write(b'\n' + json.dumps(sensor).encode())
                else:
                    firstline = False
                    await f.write(json.dumps(sensor).encode())
        self._prtgsensors[0] = await aiofiles.os.path.getmtime(self.prtgsensorsfile)
        self._prtgsensors[1] = await aiofiles.os.path.getsize(self.prtgsensorsfile)

    async def save_viewsettings(self,key,viewsettings=None):
        #remove duplicate service, remove empty section
        viewfile = os.path.join(self.get_viewdir(key),"{}.json".format(key))
        if not viewsettings:
            #no customization
            await utils.removefile(viewfile)
            if key in self._views:
                del self._views[key]
            return

        #remove empty sectors
        for sector in [k for k in viewsettings.keys()]:
            if not isinstance(viewsettings[sector],set):
                viewsettings[sector] = set(viewsettings[sector])
            if not viewsettings[sector]:
                del viewsettings[sector]
        
        if not viewsettings:
            #no customization
            await utils.removefile(viewfile)
            if key in self._views:
                del self._views[key]
            return

        #check whether it is customized or not
        customized = False
        for section in self.healthchecksections:
            if section.id not in viewsettings:
                customized = True
                break
            if len(section["services"]) != len(viewsettings[section.id]):
                customized = True
                break

        #if not customized, remove the settings
        if not customized:
            #not customized
            await util.removefile(viewfile)
            if key in self._views:
                del self._views[key]
            return

        if key in self._views and self._views[key][2] == viewsettings:
            return

        #change the service set to service list
        for k in viewsettings.keys():
            viewsettings[k] = list(viewsettings[k])

        async with aiofiles.open(viewfile,'w') as f:
            await f.write(json.dumps(viewsettings,indent=4))

        #change the service list back to service set
        for k in viewsettings.keys():
            viewsettings[k] = set(viewsettings[k])

        if key in self._views:
            self._views[key][0] = await aiofiles.os.path.getmtime(viewfile)
            self._views[key][1] = await aiofiles.os.path.getsize(viewfile)
            self._views[key][2] = viewsettings
        else:
            self._views[key] = [await aiofiles.os.path.getmtime(viewfile),await aiofiles.os.path.getsize(viewfile),viewsettings]

        logger.debug("{}: Changed the settings for view({})".format(self,key))

    async def save_prtgsensorsettings(self,key,sensorsettings=None):
        #remove duplicate service, remove empty section
        sensorfile = os.path.join(self.prtgsensorsdir,"{}.json".format(key))
        if not sensorsettings:
            #no customization
            await utils.removefile(sensorfile)
            if key in self._prtgsensorsconfig:
                del self._prtgsensorsconfig[key]
            return

        for sector in [k1 for k1 in sensorsettings.keys()]:
            sectorsettings = sensorsettings[sector]
            for service in [k2 for k2 in sectorsettings.keys()]:
                if not isinstance(sectorsettings[service],set):
                    sectorsettings[service] = set(sectorsettings[service])
                if not sectorsettings[service]:
                    del sectorsettings[service]
            if not sectorsettings:
                del sensorsettings[sector]
        
        if not sensorsettings:
            #no customization
            await utils.removefile(sensorfile)
            if key in self._prtgsensorsconfig:
                del self._prtgsensorsconfig[key]
            return

        if key in self._prtgsensorsconfig and self._prtgsensorsconfig[key][2] == sensorsettings:
            return

        #change the service set to service list
        for sectorsettings in sensorsettings.values():
            for k in sectorsettings.keys():
                sectorsettings[k] = list(sectorsettings[k])

        async with aiofiles.open(sensorfile,'w') as f:
            await f.write(json.dumps(sensorsettings,indent=4))

        #change the service list back to service set
        for sectorsettings in sensorsettings.values():
            for k in sectorsettings.keys():
                sectorsettings[k] = set(sectorsettings[k])

        if key in self._prtgsensorsconfig:
            self._prtgsensorsconfig[key][0] = await aiofiles.os.path.getmtime(sensorfile)
            self._prtgsensorsconfig[key][1] = await aiofiles.os.path.getsize(sensorfile)
            self._prtgsensorsconfig[key][2] = sensorsettings
        else:
            self._prtgsensorsconfig[key] = [await aiofiles.os.path.getmtime(sensorfile),await aiofiles.os.path.getsize(sensorfile),sensorsettings]

        logger.debug("{}: Changed the settings for prtg sensor({})".format(self,key))

    async def get_viewmeta(self,viewid):
        if "@" in viewid:
            return UserViewMeta(viewid,self)
        else:
            return next((v for v in await self.get_systemviews() if v.id == viewid),None)

    async def get_prtgsensormeta(self,sensorid):
        return next((v for v in await self.get_prtgsensors() if v.id == sensorid),None)

    async def get_view(self,viewid):
        if not viewid:
            return self
        viewmeta = await self.get_viewmeta(viewid)
        viewsettings = await self.get_viewsettings(viewid)
        return HealthCheckView(self,viewmeta,viewsettings)

    async def get_prtgsensor(self,sensorid):
        if not sensorid:
            raise Exception("Missing sensor id.")
        sensormeta = await self.get_prtgsensormeta(sensorid)
        if not sensormeta:
            raise Exception("PRTG sensor({}) doesn't exist.".format(sensorid))

        sensorsettings = await self.get_prtgsensorsettings(sensorid)
        return HealthCheckPRTGSensor(self,sensormeta,sensorsettings)


