import re
import traceback
import logging
import httpx
import inspect
import urllib.parse
#import urllib3
from datetime import datetime,timedelta,UTC
from zoneinfo import ZoneInfo
from collections import UserDict,OrderedDict

from .. import checks
from .. import settings
from .. import utils

logger = logging.getLogger(__name__)

class ConfigError(Exception):
    """Base exception for configuration errors."""
    pass

RESERVED_PRTG_CHANNELS = {
    "__status__": {
        "unit": "Custom",
        "customunit": "status",
        "float": 1,
        "value": 0
    }
    ,
    "__processtime__": {
        "unit": "Custom",
        "customunit": "processtime",
        "float": 1,
        "value": 0
    },
    "__attempts__": {
        "unit": "Custom",
        "customunit": "count",
        "float": 1,
        "value": 0
    }
}
RESERVED_PRTG_CHANNELNAME = {
    "__status__": lambda service:"{} Status".format(service["name"]),
    "__processtime__": lambda service: "{} Processtime".format(service["name"]),
    "__attempts__": lambda service: "{} Attempts".format(service["name"])
}

def format_template_factory(template,args):
    async def _func(service):
        if isinstance(args,list):
            data = []
            for arg in args:
                if callable(arg):
                    data.append(arg(service))
                    if inspect.iscoroutinefunction(arg):
                        data.append(await arg(service))
                    else:
                        data.append(arg(service))
                else:
                    data.append(arg)
            return template.format(*data)
        else:
            data = {}
            for k,v in args.items():
                if callable(v):
                    if inspect.iscoroutinefunction(v):
                        data[k] = await v(service)
                    else:
                        data[k] = v(service)
                else:
                    data[k] = v
            return template.format(**data)

    return _func

def get_datetime_factory(time,offset,timezone,pattern):
    """
    time: only support 'now', 'interval_start' and 'interval_end'
    Format the specified time with specified timezone and pattern
    """
    if timezone == "UTC":
        timezone = UTC
    else:
        timezone = ZoneInfo(timezone)

    def _func(service):
        if time == "now":
            dt = utils.now()
        elif time == "interval_start":
            dt = service.get_currentinterval_starttime()
        elif time == "interval_end":
            dt = service.get_currentinterval_starttime() + timedelta(seconds=service.interval)
        else:
            raise Exception("Time({}) Not Support".format(time))

        return utils.format_time(dt,pattern=pattern,timezone=timezone)

    return _func

def get_request_value_factory(request,prop="value"):
    async def _func(service):
        return request.get_value(prop)

    return _func

ID_RE = re.compile("^[a-zA-Z0-9\\-_]+$")
class SectionHealthCheckConfig(UserDict):
    """
    The healthcheck section configuration

    """
    SERVICEHEALTHCHECK_CLS = None
    def __init__(self,healthcheck,sectionindex,config):
        super().__init__(config)
        self.healthcheck = healthcheck
        self.sectionindex = sectionindex
        self.init_configs()

    def init_configs(self):
        if not self.get("id"):
            raise ConfigError("Section {}: Missing section id property('id').".format(self.sectionindex))
            
        if not self.get("services") and not self.get("subrequests"):
            raise ConfigError("Section {}({}): No services and subrequests are configured".format(self.sectionindex,self.id))

        self["name"] = self.get("name") or self.id

        self["enabled"] = True if self.get("enabled",True) else False

        #init baseurl
        #if not configured, set it to None
        baseurl = self.get("baseurl")
        baseurl = baseurl.strip() if baseurl else None
        if baseurl and baseurl.endswith("/"):
            baseurl = baseurl[:-1]
        if baseurl:
            self["baseurl"] = baseurl
        else:
            self["baseurl"] = None

        #init the checking time to list of [start seconds, end seconds,start time string,end time string]
        #if not configured or is all day, set it to None
        checkingtime = self.get("checkingtime")
        try:
            checkingtime = checks.parse_checkingtime(checkingtime) if checkingtime else None
        except Exception as ex:
            raise ConfigError("Section {}({}): The checktime({}) is incorrect.{}".format(self.sectionindex,self.id,checkingtime,str(ex)))
        self["checkingtime"] = checkingtime

        #init offset to seconds
        #default offset is 0
        offset = self.get("offset") or 0
        try:
            if not isinstance(offset,int):
                offset = int(str(offset).strip())
        except Exception as ex:
            raise ConfigError("Section {}({}): The offset({}) is incorrect.{}".format(self.sectionindex,self.id,offset,str(ex)))
        self["offset"] = offset

        #init interval to seconds
        #if not configured,set it to None
        interval = self.get("interval")
        try:
            if not interval:
                interval = None
            else:
                if not isinstance(interval,int):
                    interval = int(str(interval).strip())
                if interval < 0:
                    raise ConfigError("Section {}({}): The proprety 'interval'({}) is not an positive integer.".format(self.sectionindex,self.id,interval))
        except ConfigError as ex:
            raise
        except Exception as ex:
            raise ConfigError("Section {}({}): The proprety 'interval'({}) is not an integer.".format(self.sectionindex,self.id,interval))
        self["interval"] = interval

        #init timeout to seconds
        #if not configured,set it to settings.HEALTHCHECKSERVICE_TIMEOUT
        timeout = self.get("timeout")
        try:
            if timeout is None:
                timeout = settings.HEALTHCHECKSERVICE_TIMEOUT
            else:
                if not isinstance(timeout,int):
                    timeout = int(str(timeout).strip())
                if timeout < 0:
                    raise ConfigError("Section {}({}): The proprety 'timeout'({}) is not an positive integer.".format(self.sectionindex,self.id,timeout))
        except ConfigError as ex:
            raise
        except Exception as ex:
            raise ConfigError("Section {}({}): The property 'timeout'({}) is not an integer.".format(self.sectionindex,self.id,timeout))
        self["timeout"] = timeout

        #init retry to int
        #if not configured or incorrect,set it to settings.HEALTHCHECKSERVICE_RETRY
        retry = self.get("retry")
        try:
            if retry is None:
                retry = settings.HEALTHCHECKSERVICE_RETRY
            else:
                if not isinstance(retry,int):
                    retry = int(str(retry).strip())
                if retry <= 0:
                    retry = settings.HEALTHCHECKSERVICE_RETRY
        except Exception as ex:
            raise ConfigError("Section {}({}): The property 'retry'({}) is not an integer.".format(self.sectionindex,self.id,retry))
        self["retry"] = retry

        #convert the retry interval from milliseconds to seconds
        #if not configured or incorrect,set it to settings.HEALTHCHECKSERVICE_RETRY_INTERVAL
        retryinterval = self.get("retry_interval")
        try:
            if not retryinterval:
                retryinterval = settings.HEALTHCHECKSERVICE_RETRY_INTERVAL
            elif not isinstance(retryinterval,int):
                retryinterval = int(str(retryinterval).strip()) / 1000
            else:
                retryinterval = retryinterval / 1000

            if retryinterval <= 0:
                retryinterval = settings.HEALTHCHECKSERVICE_RETRY_INTERVAL
        except Exception as ex:
            raise ConfigError("Section {}({}): The property 'retry_interval'({}) is not an integer.".format(self.sectionindex,self.id,retry_interval))
        self["retry_interval"] = retryinterval

        method = self.get("method")
        if method:
            method = method.upper()
            if method not in ["GET","POST","DELETE","PUT"]:
                raise ConfigError("Section {}({}): The method({}) doesn't support.".format(self.sectionindex,self.id,method))
        else:
            method = "GET"
        self["method"] = method

        sslverify = self.get("sslverify",True)
        if sslverify is None:
            sslverify = True
        elif not isinstance(sslverify,bool):
            sslverify = str(sslverify).lower() == "true"
        self["sslverify"] = sslverify
    
        historyexpire = self.get("historyexpire") or 0
        try:
            if not isinstance(historyexpire,int):
                historyexpire = int(str(historyexpire).strip())
            if historyexpire < 0:
                historyexpire = 0
        except Exception as ex:
            raise ConfigError("Section {}({}): The historyexpire({}) is not an integer.{}: {}".format(self.sectionindex,self.id,historyexpire,ex.__class__.__name__,str(ex)))
        self["historyexpire"] = historyexpire

        errorhistoryexpire = self.get("errorhistoryexpire")
        try:
            if errorhistoryexpire is None:
                errorhistoryexpire = historyexpire
            elif not isinstance(errorhistoryexpire,int):
                errorhistoryexpire = int(str(errorhistoryexpire).strip())
            if errorhistoryexpire < 0:
                errorhistoryexpire = historyexpire
        except Exception as ex:
            raise ConfigError("Section {}({}): The errorhistoryexpire({}) is not an integer.{}: {}".format(self.sectionindex,self.id,errorhistoryexpire,ex.__class__.__name__,str(ex)))
        self["errorhistoryexpire"] = errorhistoryexpire

        healthdetailpersistent = self.get("healthdetailpersistent")
        if healthdetailpersistent:
            if isinstance(healthdetailpersistent,str) and healthdetailpersistent.lower() == "__all__":
                healthdetailpersistent = set(["green","yellow","red","error"])
            else:
                if isinstance(healthdetailpersistent,str):
                    healthdetailpersistent = [s for s in healthdetailpersistent.split(",")]
                data = set()
                for s in healthdetailpersistent:
                    s = s.strip().lower()
                    if not s:
                        continue
                    if s not in ["green","yellow","red","error"]:
                        raise ConfigError("Section {}({}): The health status({}) in property('healthdetailpersistent') doesn't' support".format(self.sectionindex,self.id,s))
                    else:
                        data.add(s)
                healthdetailpersistent = data
        else:
            healthdetailpersistent = set(["yellow","red","error"])
        self["healthdetailpersistent"] = healthdetailpersistent

        prtgconfigs = self.get("prtg")
        if prtgconfigs:
            if isinstance(prtgconfigs,list):
                if len(prtgconfigs) == 0:
                    prtgconfigs = None
            else:
                prtgconfigs = [prtgconfigs]
            if prtgconfigs:
                #initialize the prtg config
                #convert list to map
                configs = {}
                for prtgconfig in prtgconfigs:
                    prtgconfig["unit"] = prtgconfig.get("unit") or "Custom"
                    if  prtgconfig["unit"].lower() == "custom":
                        prtgconfig["customunit"] = prtgconfig.get("customunit") or "status"
                    elif "customunit" in prtgconfig:
                        del  prtgconfig["customunit"]

                    prtgconfig["value"] = prtgconfig.get("value") or 0
                    if "id" in prtgconfig:
                        channelid = prtgconfig.pop("id")
                        if not ID_RE.search(channelid):
                            raise ConfigError("Section {0}({1}): The channel id({2}) can only contain letters, numbers,'-' and '_'".format(self.sectionindex,self.id,channelid))

                    else:
                        raise ConfigError("Section {0}({1}): Missing property 'id' in prtg config".format(self.sectionindex,self.id))

                    configs[channelid] = prtgconfig

                    #init the lambda expression
                    for k in prtgconfig.keys():
                        v = prtgconfig[k]
                        if not v or not isinstance(v,str):
                            continue
                        v = v.strip()
                        if v.startswith("lambda"):
                            try:
                                prtgconfig[k] = eval(v)
                            except:
                                raise ConfigError("Section {0}({1}): the lambda expression({3}) of the prtg data key({2}) is invalid.".format(self.sectionindex,self.id,k,v))

                prtgconfigs = configs

        #add the reserved prtg data to prtgconfig
        if prtgconfigs:
            for k,v in RESERVED_PRTG_CHANNELS.items():
                if k in prtgconfigs:
                    #configured , ignore the RESERVED config
                    continue
                prtgconfigs[k] = v
        else:
            prtgconfigs = RESERVED_PRTG_CHANNELS
        self["prtg"] = prtgconfigs

        services = OrderedDict()
        serviceindex = 0
        for service in self["services"]:
            serviceindex += 1
            service = self.SERVICEHEALTHCHECK_CLS(self.healthcheck,self,serviceindex,service)
            if service.url and not service["healthchecks"]:
                continue
            services[service.id] = service

        self["services"] = services


    @property
    def id(self):
        return self["id"]

    @property
    def name(self):
        return self["name"]

    @property
    def baseurl(self):
        """
        Can be None
        """
        return self["baseurl"]

    @property
    def enabled(self):
        return self["enabled"]

    @property
    def checkingtime(self):
        """
        Reuturn None if not configration, or can check all day; Otherwise, return a list of time range[start check time(seconds in the day),end check time (seconds in the day),start check time(str),end check time(str)]
        """
        return self["checkingtime"]

    @property
    def offset(self):
        """
        An integer
        Default is 0
        """
        return self["offset"]

    @property
    def headers(self):
        return self.get("headers") or {}

    @property
    def interval(self):
        """
        seconds or None
        """
        return self["interval"]

    @property
    def timeout(self):
        """
        Seconds
        Default is settings.HEALTHCHECKSERVICE_TIMEOUT
        """
        return self["timeout"]

    @property
    def retry(self):
        """
        Integer
        Default is settings.HEALTHCHECKSERVICE_RETRY
        """
        return self["retry"]

    @property
    def retry_interval(self):
        """
        Already converted to seconds
        Default is settings.HEALTHCHECKSERVICE_RETRY_INTERVAL
        """
        return self["retry_interval"]

    @property
    def user(self):
        """
        Can be None
        """
        return self.get("user")

    @property
    def password(self):
        """
        Can be None
        """
        return self.get("password")

    @property
    def method(self):
        """
        Default is GET
        """
        return self["method"]

    @property
    def formdata(self):
        """
        Can be None
        """
        return self.get("data")

    @property
    def queryparameters(self):
        """
        Default is {}
        """
        return self.get("queryparameters") or {}

    @property
    def sslverify(self):
        return self["sslverify"]

    @property
    def historyexpire(self):
        """
        Default is 0
        """
        return self["historyexpire"]

    @property
    def errorhistoryexpire(self):
        """
        Default is 0
        """
        return self["errorhistoryexpire"]

    @property
    def healthdetailpersistent(self):
        """
        Default is ["yellow","red","error"])
        """
        return self["healthdetailpersistent"]

    @property
    def prtg(self):
        """
        At least have reserved prtg channels
        """
        return self["prtg"]

    @property
    def services(self):
        return self["services"]

    @property
    def healthcheckservices(self):
        return self["services"].values()

    @property
    def prtgenabled(self):
        return any(s.prtgenabled for s in self.healthcheckservices)

class RequestConfig(UserDict):
    def __init__(self,healthcheck,section,configindex,data):
        super().__init__(data)
        self.healthcheck = healthcheck
        self.section = section
        self.configindex = configindex

        self.init_configs()

    #@datetime(TZ?#pattern?)?:now|interval_start+10d|h|m|s
    datetime_re =re.compile("^\\s*@datetime(\\((?P<timezone>[^#\\)]+)?(#(?P<pattern>[^\\)]+)?)?\\))?\\s*:\\s*(?P<time>(now|interval_start))(\\s*(?P<offset_direction>[+-])\\s*(?P<offset>\\d+)\\s*(?P<offset_unit>[dhms])?)?\\s*$")
    #@request:(section.)?request(prop?)
    request_re =re.compile("^\\s*@request\\s*:\\s*((?P<sector>[^\\.\\s]+)\\.)?(?P<request>[^\\s\\(\\.]+)(\\(\\s*(?P<prop>[^\\s\\)]+)?\\s*\\))?\\s*$")
    def _init_data_arg(self,arg):
        if isinstance(arg,str):
            if arg.startswith("lambda "):
                #is lambda expression
                return eval(arg)
            else:
                m = self.datetime_re.search(arg)
                if m:
                    #dynamic datetime
                    timezone = m.group("timezone") or settings.TZ
                    pattern = m.group("pattern") or "%Y-%m-%dT%H:%M:%S"
                    time = m.group("time")
                    offset_direction = m.group("offset_direction") or "+"
                    offset = m.group("offset")
                    offset_unit = m.group("offset_unit") or "s"
                    if offset:
                        #convert to seconds
                        if offset_unit == "d":
                            offset *= 86400 
                        elif offset_unit == "h":
                            offset *= 3600
                        elif offset_unit == "m":
                            offset *= 60

                        if offset_direction == "-":
                            offset = offset * -1
                    else:
                        offset = 0
                    return get_datetime_factory(time,offset,timezone,pattern)
                m = self.request_re.search(arg)
                if m:
                    #dynamic value extract from http response
                    section = m.group("section") or self.sectionid
                    request = self.healthcheck.get_subrequest(section,m.group("request"))
                    prop = m.group("prop") or "value"
                    if request:
                        return get_request_value_factory(request,prop)
            return arg
        else:
            return arg

    def _init_data(self,data):
        if isinstance(data,list):
            for i in range(len(data)):
                data[i] = self._init_data(data[i])
        elif isinstance(data,dict):
            if data.get("__template__"):
                #is a dyanmic value
                if data.get("__args__"):
                    #template string
                    if isinstance(data,list):
                        for i in range(len(data["__args__"])):
                            data["__args__"][i] = self._init_data_arg(data["__args__"][i])
                    else:
                        for k in data["__args__"].keys():
                            data["__args__"][k] = self._init_data_arg(data["__args__"][k])
                    data = format_template_factory(data["__template__"],data["__args__"])
                else:
                    #template without args
                    #use the template directly
                    data = data["__template__"]
            else:
                #is a normal dict object
                for k in data.keys():
                    data[k] = self._init_data(data[k])
        else:
            #is a normal data
            data = self._init_data_arg(data)

        return data

    def _data_is_dynamic(self,data):
        if isinstance(data,list):
            for o in data:
                if self._data_is_dynamic(o):
                    return True
        elif isinstance(data,dict):
            if data.get("__template__"):
                return True

            for v in data.values():
                if self._data_is_dynamic(v):
                    return True
        else:
            if  self._init_data_arg(data) is not data:
                return True
        
        return False

    def init_configs(self):
        if not self.get("id"):
            raise ConfigError("{0} {1}({2}).{3}: Missing property(id)".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex))

        if "user" not in self:
            self["user"] = self.section.user
    
        if "password" not in self:
            self["password"] = self.section.password
    
        if "sslverify" not in self:
            self["sslverify"] = self.section.sslverify
        elif not isinstance(self["sslverify"],bool):
            self["sslverify"] = str(self["sslverify"]).lower() == "true"
    
        method = self.get("method")
        if method:
            method = method.upper()
            if method not in ["GET","POST","DELETE","PUT"]:
                raise ConfigError("{0} {1}({2}).{3}({4}): The method({5}) doesn't support".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,method))
        else:
            method = self.section.method
        self["method"] = method

        if "data" not in self:
            self["data"] = self.section.formdata

        location = self.get("location")
        location = location.strip() if location else None
        if not location and not self.section.baseurl:
            #no url
            location = None
        elif not location:
            location = self.section.baseurl
        elif not location.startswith("http"):
            if self.section.baseurl:
                if location.startswith("/"):
                    location = "{}{}".format(self.section.baseurl,location)
                else:
                    location = "{}/{}".format(self.section.baseurl,location)
            else:
                raise ConfigError("{0} {1}({2}).{3}({4}): Location({5}) is not a valid url".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,location))

        #init query parameters
        #query parameters support dyanmic parameter
        queryparameters = self.get_queryparameters()
        fixedparameters = None
        dynamicparameters = None
        if any(self._data_is_dynamic(v) for v in queryparameters.values()):
            #has some dynamic parameters
            if queryparameters is self.section.queryparameters:
                #service uses section's queryparameters
                fixedparameters = {}
                dynamicparameters = {}
                for k,v in queryparameters.items():
                    if self._data_is_dynamic(v):
                        dynamicparameters[k] = self._init_data(v)
                    else:
                        fixedparameters[k] = v
            else:
                #service has its own query parameters
                fixedparameters = {}
                for k in queryparameters.keys():
                    if self._data_is_dynamic(queryparameters[k]):
                        queryparameters[k] = self._init_data(queryparameters[k])
                    else:
                        fixedparameters[k] = v
                #remove the fixed parameters
                for k in fixedparameters.keys():
                    del queryparameters[k]

                dynamicparameters = queryparameters
        else:
            #no dynamic parameters
            fixedparameters = queryparameters
            dynamicparameters = None

        if dynamicparameters: 
            self["dynamicqueryparameters"] = dynamicparameters
            self["queryparameters"] = {}
        else:
            self["dynamicqueryparameters"] = None
            self["queryparameters"] = None

        if fixedparameters:
            if "?" in location:
                location = "{}&{}".format(location,urllib.parse.urlencode(fixedparameters))
            else:
                location = "{}?{}".format(location,urllib.parse.urlencode(fixedparameters))
        self["location"] = location

        #init headers
        #headers support dynamic header.
        headers = self.get_headers()
        fixedheaders = None
        dynamicheaders = None
        if any(self._data_is_dynamic(v) for v in headers.values()):
            #has some dynamic headers
            if headers is self.section.headers:
                #service uses section's headers
                fixedheaders = {}
                dynamicheaders = {}
                for k,v in headers.items():
                    if self._data_is_dynamic(v):
                        dynamicheaders[k] = self._init_data(v)
                    else:
                        fixedheaders[k] = v
            else:
                #service has its own query headers
                fixedheaders = {}
                for k in headers.keys():
                    if self._data_is_dynamic(headers[k]):
                        headers[k] = self._init_data(headers[k])
                    else:
                        fixedheaders[k] = v
                #remove the fixed headers
                for k in fixedheaders.keys():
                    del headers[k]

                dynamicheaders = headers
        else:
            #no dynamic headers
            fixedheaders = headers
            dynamicheaders = None

        dynamicheaders = dynamicheaders or None
        #if no dynamicheaders, set it to None
        self["dynamicheaders"] = dynamicheaders
        if dynamicheaders:
            self["headers"] = fixedheaders
        elif fixedheaders:
            #only has fixed headers
            self["headers"] = fixedheaders
        else:
            #no headers
            self["headers"] = None

        #init formdata
        #formdata support dynamic data
        data = self.get_formdata()
        fixeddata = None
        dynamicdata = None
        if not data:
            fixeddata = None
            dynamicdata = None
        elif any(self._data_is_dynamic(v) for v in data.values()):
            #has some dynamic data
            if data is self.section.data:
                #service uses section's data
                fixeddata = {}
                dynamicdata = {}
                for k,v in data.items():
                    if self._data_is_dynamic(v):
                        dynamicdata[k] = self._init_data(v)
                    else:
                        fixeddata[k] = v
            else:
                #service has its own query data
                fixeddata = {}
                for k in data.keys():
                    if self._data_is_dynamic(data[k]):
                        data[k] = self._init_data(data[k])
                    else:
                        fixeddata[k] = v
                #remove the fixed data
                for k in fixeddata.keys():
                    del data[k]

                dynamicdata = data
        else:
            #no dynamic data
            fixeddata = data
            dynamicdata = None

        dynamicdata = dynamicdata or None
        #if no dynamicdata, set it to None
        self["dynamicdata"] = dynamicdata
        if dynamicdata:
            self["data"] = fixeddata
        elif fixeddata:
            #only has fixed data
            self["data"] = fixeddata
        else:
            #no data
            self["data"] = None

        try:
            timeout = self.get("timeout")
            if timeout is None:
                timeout = self.section.timeout
            elif not isinstance(timeout,int):
                timeout = int(str(timeout).strip())

            if timeout < 0:
                raise ConfigError("{0} {1}({2}).{3}({4}): The property 'timeout'({5}) is not a positive integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,self.get("timeout")))
        except ConfigError as ex:
            raise
        except Exception as ex:
            raise ConfigError("{0} {1}({2}).{3}({4}): The property 'timeout'({5}) is not an integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,self.get("timeout")))
        self["timeout"] = timeout
        self["request_timeout"] = httpx.Timeout(connect = settings.CONNECT_TIMEOUT / 1000.0, read=timeout / 1000.0,write=timeout / 1000.0,pool=timeout/1000.0)

    @property
    def sectionid(self):
        return self.section.id

    @property
    def id(self):
        return self['id']

    @property
    def user(self):
        return self["user"]

    @property
    def password(self):
        return self["password"]

    _auth = None
    @property
    def auth(self):
        if not self._auth:
            if self.user:
                self._auth = (self.user,self.password)
            else:
                return None

        return self._auth

    @property
    def sslverify(self):
        return self["sslverify"]

    @property
    def method(self):
        return self['method']

    @property
    def url(self):
        return self['location']

    @property
    def headers(self):
        if not self["dynamicheaders"]:
            return self["headers"]
        headers = self["headers"]
        for k,v in self.self["dynamicheaders"].items():
            headers[k] = v(self)

        return headers

    @property
    def queryparameters(self):
        if not self["dynamicqueryparameters"]:
            return None
        parameters = self["queryparameters"]
        for k,v in self.self["dynamicqueryparameters"].items():
            parameters[k] = v(self)

        return parameters

    @property
    def formdata(self):
        if not self["dynamicdata"]:
            return self["data"]
        data = self["data"]
        for k,v in self.self["dynamicdata"].items():
            data[k] = v(self)

        return data

    @property
    def request_timeout(self):
        return self["request_timeout"]

    @property
    def timeout(self):
        return self["timeout"]

    @property
    def sslverify(self):
        return self["sslverify"]

class SubrequestConfig(RequestConfig):
    def __init__(self,healthcheck,section,configindex,data):
        super().__init__(healthcheck,section,configindex,data)
        self._value = {}
        self._expireat = None

    def __str__(self):
        return "Subrequest:{}.{}.{}".format(self.healthcheck,self.sectionid,self.requestid)


    def get_queryparameters(self):
        return self.get("queryparameters") or {}

    def get_headers(self):
        return self.get("headers") or {}

    def get_formdata(self):
        return self.get("data") or {}

    @property
    def expired(self):
        if self._value is None:
            return True
        if self._expireat is None:
            return 

    async def get_value(self,prop):
        if not self._value  or self._expireat is None or utils.now() >= self._expireat:
            self._value.clear()
            self._expireat = None

            try:
                res = None
                #logger.debug("Start to run the subrequest({})".format(self))
                async with httpx.AsyncClient(auth=self.auth,timeout=self.request_timeout,verify=self.sslverify,headers=self.headers) as client:
                    if self.servicehealthcheck.method == "GET":
                        func = client.get
                    elif self.servicehealthcheck.method == "POST":
                        func = client.post
                        res = await func(self.url,data=self.data)
                    elif self.servicehealthcheck.method == "PUT":
                        func = client.put
                        res = await func(self.url,data=self.data)
                    elif self.servicehealthcheck.method == "DELETE":
                        func = client.delete
                    else:
                        #Not support
                        raise Exception("Http method({}) Not Support".format(self.method))
                    if data:
                        res = await func(self.url,data=data)
                    else:
                        res = await func(self.url)
            finally:
                endtime = utils.now()
                processtime = (endtime - starttime).total_seconds() * 1000

        return self.value.get(prop)

class ServiceHealthCheckConfig(RequestConfig):
    selected = False


    def get_queryparameters(self):
        queryparameters = self.get("queryparameters")
        if not queryparameters:
            queryparameters = self.section.queryparameters
        elif self.section.queryparameters:
            for k,v in self.section.queryparameters.items():
                if k not in queryparameters:
                    queryparameters[k] = v
                elif queryparameters[k] is None:
                    del queryparameters[k]
        return queryparameters

    def get_headers(self):
        headers = self.get("headers")
        if not headers:
            headers = self.section.headers
        elif self.section.headers:
            for k,v in self.section.headers.items():
                if k not in headers:
                    headers[k] = v
                elif headers[k] is None:
                    del headers[k]
        return headers

    def get_formdata(self):
        data = self.get("data")
        if not data:
            data = self.section.formdata
        elif self.section.formdata:
            for k,v in self.section.formdata.items():
                if k not in data:
                    data[k] = v
                elif data[k] is None:
                    del data[k]
        return data

    def init_configs(self):
        super().init_configs()
        if not self.get("name"):
            self["name"] = self.id

        self["enabled"] = True if self.get("enabled",True) else False

        checkingtime = self.get("checkingtime")
        if checkingtime:
            try:
                checkingtime = checks.parse_checkingtime(checkingtime)
            except Exception as ex:
                raise ConfigError("{0} {1}({2}).{3}({4}): The checktime({5}) is incorrect.{6}".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,checkingtime,str(ex)))
        else:
            checkingtime = self.section.checkingtime
        self["checkingtime"] = checkingtime

        try:
            offset = int(self.get("offset") or self.section.offset)
        except Exception as ex:
            raise ConfigError("{0} {1}({2}).{3}({4}): The offset({5}) is incorrect.{6}".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,self.get("offset"),str(ex)))
        self["offset"] = offset

        interval = self.get("interval")
        try:
            if interval:
                if not isinstance(interval,int):
                    interval = int(str(interval).strip())
                if interval <= 0:
                    raise ConfigError("{0} {1}({2}).{3}({4}): The property 'interval'({5}) is not a positive integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,interval))
            elif not self.section.interval:
                raise ConfigError("{0} {1}({2}).{3}({4}): Missing property(interval)".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id))
            else:
                interval = self.section.interval
        except Exception as ex:
            raise ConfigError("{0} {1}({2}).{3}({4}): The property 'interval'({5}) is not an integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,interval))
        self["interval"] = interval

        retry = self.get("retry")
        try:
            if retry is None:
                retry = self.section.retry
            elif not isinstance(retry,int):
                retry = int(str(retry).strip())
            if retry <= 0:
                raise ConfigError("{0} {1}({2}).{3}({4}): The property 'retry'({5}) is not a positive integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,retry))
        except ConfigError as ex:
            raise
        except Exception as ex:
            raise ConfigError("{0} {1}({2}).{3}({4}): The property 'retry'({5}) is not an integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,retry))
        self["retry"] = retry

        retryinterval = self.get("retry_interval")
        try:
            if not retryinterval:
                retryinterval = self.section.retry_interval
            elif not isinstance(retryinterval,int):
                retryinterval = int(str(retryinterval).strip()) / 1000
            else:
                retryinterval = retryinterval / 1000
            if retryinterval <= 0 :
                raise ConfigError("{0} {1}({2}).{3}({4}): The property 'retry_interval'({5}) is not a positive integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,retry_interval))
        except ConfigError as ex:
            raise
        except Exception as ex:
            raise ConfigError("{0} {1}({2}).{3}({4}): The property 'retry_interval'({5}) is not an integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,retry_interval))
        self["retry_interval"] = retryinterval

        self["max_retry_processtime"] = (settings.CONNECT_TIMEOUT + self.timeout) / 1000.0 + retryinterval

        historyexpire = self.get("historyexpire")
        try:
            if historyexpire is not None:
                if not isinstance(historyexpire,int):
                    historyexpire = int(str(historyexpire).strip())
                if historyexpire < 0:
                    historyexpire = 0
            else:
                historyexpire = self.section.historyexpire
        except Exception as ex:
            raise ConfigError("{0} {1}({2}).{3}({4}): The property 'historyexpire'({5}) is not an integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,historyexpire))
        self["historyexpire"] = historyexpire

        errorhistoryexpire = self.get("errorhistoryexpire")
        try:
            if errorhistoryexpire is not None:
                if not isinstance(errorhistoryexpire,int):
                    errorhistoryexpire = int(str(errorhistoryexpire).strip())
                if errorhistoryexpire < 0:
                    errorhistoryexpire = 0
            elif self.section.errorhistoryexpire:
                errorhistoryexpire = self.section.errorhistoryexpire
            else:
                errorhistoryexpire = historyexpire
        except Exception as ex:
            raise ConfigError("{0} {1}({2}).{3}({4}): The property 'errorhistoryexpire'({5}) is not an integer".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,errorhistoryexpire))
        self["errorhistoryexpire"] = errorhistoryexpire

        healthdetailpersistent = self.get("healthdetailpersistent")
        if healthdetailpersistent is not None:
            if not healthdetailpersistent:
                healthdetailpersistent = []
            elif isinstance(healthdetailpersistent,str) and healthdetailpersistent.lower() == "__all__":
                healthdetailpersistent = ["green","yellow","red","error"]
            else:
                if isinstance(healthdetailpersistent,str):
                    healthdetailpersistent = [s for s in healthdetailpersistent.split(",")]
                data = set()
                for s in healthdetailpersistent:
                    s = s.strip().lower()
                    if not s:
                        continue
                    if s not in ["green","yellow","red","error"]:
                        raise ConfigError("{0} {1}({2}).{3}({4}): The health status({5}) in property('healthdetailpersistent') doesn't' support".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,s))
                    else:
                        data.add(s)
                healthdetailpersistent = data
        else:
            healthdetailpersistent = self.section.healthdetailpersistent
        self["healthdetailpersistent"] = healthdetailpersistent

        #merge the sector's prtg config into service's prtg config
        reserved_prtg_channelids = set(RESERVED_PRTG_CHANNELS.keys())
        reserved_prtg_channelnames = dict()
        for k,func in RESERVED_PRTG_CHANNELNAME.items():
            reserved_prtg_channelnames[k] = func(self)

        #merge the prtg config with section's prtg config, and covert it to a list of prtg config if have prtg config
        prtgconfigs = self.get("prtg")
        if prtgconfigs:
            if not isinstance(prtgconfigs,list):
                prtgconfigs = [prtgconfigs]

            for prtgconfig in prtgconfigs:
                if "id" in prtgconfig:
                    channelid = prtgconfig.get("id")
                    if not ID_RE.search(channelid):
                        raise ConfigError("{0} {1}({2}).{3}({4}): The channel id({5}) can only contain letters, numbers,'-' and '_'".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,channelid))
                elif len(self["prtg"]) == 1:
                    #only have one channle, use serviceid as default channle id
                    prtgconfig["id"] == self.id
                else:
                    raise ConfigError("{0} {1}({2}).{3}({4}): Missing property 'id' in prtg config({5})".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,prtgconfig))

                #init lambda expression
                for k in prtgconfig.keys():
                    v = prtgconfig[k]
                    if not v:
                        continue
                    if not isinstance(v,str):
                        continue
                    v = v.strip()
                    if v.startswith("lambda"):
                        try:
                            prtgconfig[k] = eval(v)
                        except:
                            raise ConfigError("{0} {1}({2}).{3}({4}): The lambda expression({6}) of the prtg data key({5}) is invalid.".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,k,v))

                #find the baseconfig
                baseconfig = self.section.prtg.get(prtgconfig["id"]) if self.section.prtg else None

                if not baseconfig:
                    continue
                #add the base config to service prtg config if it doesn't exist
                for k,v in baseconfig.items():
                    if k not in prtgconfig:
                        prtgconfig[k] = v
        else:
            prtgconfigs = None

        #init prtg config and convert it to config map
        prtgconfigmap = OrderedDict()
        if prtgconfigs:
            #initialize the final prtg config
            #convert the prtg config from list to dict
            for prtgconfig in prtgconfigs:
                #init the normal prtg property
                prtgconfig["channel"] = prtgconfig.get("channel") or self.name
                prtgconfig["unit"] = prtgconfig.get("unit") or "Custom"

                if  prtgconfig["unit"].lower() == "custom":
                    prtgconfig["customunit"] = prtgconfig.get("customunit") or "status"
                elif "customunit" in prtgconfig:
                    del  prtgconfig["customunit"]

                prtgconfig["value"] = prtgconfig.get("value") or 0
                channelid = prtgconfig.pop("id")

                #find the function to get prtg value for each health status 
                getdata_map = {}
                for status in ("green","yellow","red","error"):
                    key = "data4{}".format(status)
                    if key in prtgconfig:
                        getdata_map[status] = prtgconfig.pop(key)

                #separate the computed properties from normal properties in prtg config
                computed_columns = {}
                for k,v in prtgconfig.items():
                    if callable(v):
                        #turn the func with two arguments to a func with one argument if required
                        computed_columns[k] = checks.lambda_func_fatctory(self,v)

                for k in computed_columns.keys():
                    del prtgconfig[k]

                prtgconfigmap[channelid] = (prtgconfig,getdata_map,computed_columns)

                #remove it from reserved prtg channel set to avoid to add this reserved prtg channel again.
                reserved_prtg_channelids.discard(channelid)
                if prtgconfig["channel"] in reserved_prtg_channelnames:
                    #the default channel name used by reserved prtg channels is configured in service prtg data, ignore the related reserved prtd data
                    reserved_prtg_channelids.discard(reserved_prtg_channelnames[prtgconfig["channel"]])

        #Add the RESERVED PRTG DATA if still has some reserved prtg channels are not added.
        for channelid in reserved_prtg_channelids:
            prtgconfig = dict(self.section.prtg[channelid])
            prtgconfig["channel"] = RESERVED_PRTG_CHANNELNAME[channelid](self)
            prtgconfigmap[channelid] = (prtgconfig,{},{})

        self["prtg"] = prtgconfigmap

        criticalweight = self.get("criticalweight")
        try:
            if criticalweight:
                if ":" in criticalweight:
                    criticalweight = [d.strip() for d in criticalweight.split(":",1)]
                    if not criticalweight[0]:
                        criticalweight[0] = "__default__"
                    criticalweight[1] = float(criticalweight[1])
                else:
                    criticalweight = ["__default__",float(criticalweight)]

            else:
                criticalweight = None
        except:
            raise ConfigError("{0} {1}({2}).{3}({4}): The property 'criticalweight'({5}) is incorrect.".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,criticalweight))
        self["criticalweight"] = criticalweight

        if self.url:
            if not self.get("healthchecks"):
                raise ConfigError("{0} {1}({2}).{3}({4}): Missing healthcheck configuration.".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id))
            if not isinstance(self["healthchecks"],dict):
                raise ConfigError("{0} {1}({2}).{3}({4}): Healthcheck is not a dictionary.".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id))
    
            for key in list(self["healthchecks"].keys()):
                try:
                    if key not in ("green","yellow","red","error"):
                        raise ConfigError("{0} {1}({2}).{3}({4}): The health status({5}) in healthchecks is not in ('green','yellow','red','error').".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,key))
    
                    if self.prtg:
                        #find the function to get prtg data from healtheck configuration.
                        prtgdata_map = {}
                        if isinstance(self["healthchecks"][key],(list,tuple)):
                            prtgdata_config = {}
                        else:
                            prtgdata_config = self["healthchecks"][key].get('prtg',{})
                            if not isinstance(prtgdata_config,dict):
                                if len(self.prtg) > 1 :
                                    raise ConfigError("{0} {1}({2}).{3}({4}).{5}: The service declares multiple prtg channels ,the prtg config({6}) should be dict type.".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,key,prtgdata_config))
                                else:
                                    channelid = next(k for k in self.prtg.keys())
                                    prtgdata_config = {channelid:prtgdata_config}
    
                        #find the function to get prtg data by checking healthcheck config first and then service's prtg configuration.
                        for channelid,prtgconfig in self["prtg"].items():
                            if channelid in prtgdata_config:
                                prtgdata_map[channelid] = checks.get_prtg_factory(self,prtgdata_config[channelid]) 
                            elif key in prtgconfig[1]:
                                prtgdata_map[channelid] = checks.get_prtg_factory(self,prtgconfig[1][key]) 
    
                        if isinstance(self["healthchecks"][key],(list,tuple)):
                            self["healthchecks"][key] = [
                                checks.init_conds(self,self["healthchecks"][key]),
                                checks.get_message_factory(self,None),
                                prtgdata_map,
                                None
                            ]
                        else:
                            self["healthchecks"][key] = [
                                checks.init_conds(self,self["healthchecks"][key].get("condition")),
                                checks.get_message_factory(self,self["healthchecks"][key].get('message')),
                                prtgdata_map,
                                checks.init_transforms(self["healthchecks"][key].get("transforms")) if self["healthchecks"][key].get("transforms") else None
                            ]
                    else:
                        if isinstance(self["healthchecks"][key],(list,tuple)):
                            self["healthchecks"][key] = [
                                checks.init_conds(self,self["healthchecks"][key]),
                                checks.get_message_factory(self,None),
                                None,
                                None
                            ]
                        else:
                            self["healthchecks"][key] = [
                                checks.init_conds(self,self["healthchecks"][key].get("condition")),
                                checks.get_message_factory(self,self["healthchecks"][key].get('message')),
                                None,
                                checks.init_transforms(self["healthchecks"][key].get("transforms")) if self["healthchecks"][key].get("transforms") else None
                            ]
                except Exception as ex:
                    traceback.print_exc()
                    raise ConfigError("{0} {1}({2}).{3}({4}): The config({5}) in healthchecks is in valid.{6}: {7}".format(self.__class__.__name__,self.section.sectionindex,self.section.id,self.configindex,self.id,key,ex.__class__.__name__,str(ex)))
    


    def __str__(self):
        return "{}.{}.{}".format(self.healthcheck,self.sectionid,self.id)

    @property
    def name(self):
        return self["name"]

    @property
    def enabled(self):
        return self["enabled"]

    @property
    def checkingtime(self):
        return self["checkingtime"]

    @property
    def offset(self):
        return self["offset"]

    @property
    def interval(self):
        return self["interval"]

    @property
    def retry(self):
        return self["retry"]

    @property
    def retry_interval(self):
        return self["retry_interval"]

    @property
    def max_retry_processtime(self):
        return self["max_retry_processtime"]

    @property
    def historyexpire(self):
        return self["historyexpire"]

    @property
    def historyenabled(self):
        return self.historyexpire > 0

    @property
    def errorhistoryexpire(self):
        return self["errorhistoryexpire"]

    @property
    def errorhistoryenabled(self):
        return self.errorhistoryexpire > 0

    @property
    def healthdetailpersistent(self):
        return self["healthdetailpersistent"]

    @property
    def prtg(self):
        return self["prtg"]

    @property
    def prtgenabled(self):
        return True if self.prtg else False

    @property
    def criticalweight(self):
        return self["criticalweight"]

    @property
    def healthchecks(self):
        return self["healthchecks"]


    @property
    def prtgchannels(self):
        """
        A iterator of (channelid,[channelconfig,get_prtgdata,computed_columns])
        """
        return self['prtg'].items() if self['prtg'] else []
