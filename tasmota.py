try:
    import collections.abc as collections
except ImportError:  # Python <= 3.2 including Python 2
    import collections

errmsg = ""
try:
    import Domoticz
except Exception as e:
    errmsg += "Exception:Domoticz core start error: "+str(e)
try:
    import json
except Exception as e:
    errmsg += " Exception:Json import error: "+str(e)
try:
    import binascii
except Exception as e:
    errmsg += " Exception:binascii import error: "+str(e)


tasmotaDebug = True


# Decide if tasmota.py debug messages should be displayed if domoticz debug is enabled for this plugin
def setTasmotaDebug(flag):
    global tasmotaDebug
    tasmotaDebug = flag


# Replaces Domoticz.Debug() so tasmota related messages can be turned off from plugin.py
def Debug(msg):
    if tasmotaDebug:
        Domoticz.Debug(msg)


# Handles incoming Tasmota messages from MQTT or Domoticz commands for Tasmota devices
class Handler:
    def __init__(self, subscriptions, prefixes, tasmotaDevices, mqttClient, devices):
        Debug("Handler::__init__(prefixes: {}, subs: {})".format(prefixes, repr(subscriptions)))

        if errmsg != "":
            Domoticz.Error(
                "Handler::__init__: Domoticz Python env error {}".format(errmsg))

        # So far only STATUS, STATE, SENSOR and RESULT are used. Others just for research...
        self.topics = ['INFO1', 'STATE', 'SENSOR', 'RESULT', 'STATUS',
                       'STATUS5', 'STATUS8', 'STATUS11', 'ENERGY']

        self.prefix = [None] + prefixes
        self.tasmotaDevices = tasmotaDevices
        self.subscriptions = subscriptions
        self.mqttClient = mqttClient

        # I don't understand variable (in)visibility
        global Devices
        Devices = devices

    def debug(self, flag):
        global tasmotaDebug
        tasmotaDebug = flag

    # Translate domoticz command to tasmota mqtt command(s?)
    def onDomoticzCommand(self, Unit, Command, Level, Color):
        Debug("Handler::onDomoticzCommand: Unit: {}, Command: {}, Level: {}, Color: {}".format(
            Unit, Command, Level, Color))

        if self.mqttClient is None:
            return False

        try:
            description = json.loads(Devices[Unit].Description)
            topic = '{}/{}'.format(description['Topic'],
                                   description['Command'])
        except:
            return False

        msg = d2t(description['Command'], Command)
        if msg is None:
            Debug("Handler::onDomoticzCommand: no message")
            return False

        try:
            self.mqttClient.publish(topic, msg)
        except Exception as e:
            Domoticz.Error("Handler::onDomoticzCommand: {}".format(str(e)))
            return False

        return True

    # Subscribe to our topics
    def onMQTTConnected(self):
        subs = []
        for topic in self.subscriptions:
            topic = topic.replace('%topic%', '+')
            subs.append(topic.replace('%prefix%', self.prefix[2]) + '/+')
            subs.append(topic.replace('%prefix%', self.prefix[3]) + '/+')
        Debug('Handler::onMQTTConnected: Subscriptions: {}'.format(repr(subs)))
        self.mqttClient.subscribe(subs)

    # Process incoming MQTT messages from Tasmota devices
    # Call Update{subtopic}Devices() if it is potentially one of ours
    def onMQTTPublish(self, topic, message):
        Debug("Handler::onMQTTPublish: topic: {}, self.topics: {}, self.subscriptions: {}".format(topic, self.topics, self.subscriptions))
        # self.topics: 'INFO1', 'STATE', 'SENSOR', 'RESULT', 'STATUS', 'STATUS5', 'STATUS8', 'STATUS11', 'ENERGY'
        # self.subscriptions: ['%prefix%/%topic%', '%topic%/%prefix%'] 
        # Check if we handle this topic tail at all (hardcoded list SENSOR, STATUS, ...)
        subtopics = topic.split('/')
        tail = subtopics[-1]
 
        if tail not in self.topics:
            return True

        # Different Tasmota devices can have different FullTopic patterns.
        # All FullTopic patterns we care about are in self.subscriptions (plugin config)
        # Tasmota devices will be identified by a hex hash from FullTopic without %prefix%

        # Identify the subscription that matches our received subtopics
        fulltopic = []
        cmndtopic = []
        for subscription in self.subscriptions:
            patterns = subscription.split('/')
            for subtopic, pattern in zip(subtopics[:-1], patterns):
                if( (pattern not in ('%topic%', '%prefix%', '+', subtopic)) or
                    (pattern == '%prefix%' and subtopic != self.prefix[2] and subtopic != self.prefix[3]) or
                    (pattern == '%topic%' and (subtopic == 'sonoff' or subtopic == 'tasmota')) ):

                    fulltopic = []
                    cmndtopic = []
                    break
                if(pattern != '%prefix%'):
                    fulltopic.append(subtopic)
                    cmndtopic.append(subtopic)
                else:
                    cmndtopic.append(self.prefix[1])
            if fulltopic != []:
                break

        if not fulltopic:
            return True

        fullName = '/'.join(fulltopic)
        cmndName = '/'.join(cmndtopic)

        # fullName should now contain all subtopic parts except for %prefix%es and tail
        # I.e. fullName is uniquely identifying the sensor or button referred by the message
        Debug("Handler::onMQTTPublish: device: {}, cmnd: {}, tail: {}, message: {}".format(
            fullName, cmndName, tail, str(message)))

        #tasmotaDevices = {'DANTEX' , 'D3_H12KW'}
        
        if fullName not in self.tasmotaDevices:
            return True
			
        if tail == 'STATE':  # POWER* status
            if updateStateDevices(fullName, cmndName, message):
                self.requestStatus(cmndName)
        elif tail == 'SENSOR':
            if updateSensorDevices(fullName, cmndName, message):
                self.requestStatus(cmndName)
        elif tail == 'RESULT':  # POWER* change
            updateResultDevice(fullName, message)
        elif tail == 'STATUS':  # Friendly names
            updateStatusDevices(fullName, cmndName, message)
        elif tail == 'INFO1':  # update module and version in device description
            updateInfo1Devices(fullName, cmndName, message)
            self.requestStatus(cmndName)
        elif tail == 'STATUS5':  # nop
            updateNetDevices(fullName, cmndName, message)
        elif tail == 'ENERGY':  # nop
            updateEnergyDevices(fullName, cmndName, message)

        return True

    # Request device STATUS via mqtt
    def requestStatus(self, cmdName):
        Debug("Handler::requestStatus: {}".format(cmdName))
        try:
            topic = '{}/{}'.format(cmdName, "STATUS")
            self.mqttClient.publish(topic, "")
        except Exception as e:
            Domoticz.Error("Exception:Handler::requestStatus: {}".format(str(e)))


###########################
# Tasmota Utility functions


# Generate a hash identifying a tasmota device as a whole. Stored as DeviceId in domoticz devices (1:n relation)
def deviceId(deviceName):
    return '{:08X}'.format(binascii.crc32(deviceName.encode('utf8')) & 0xffffffff)


# Collects a list of unit ids of all domoticz devices refering to the same tasmota device
def findDevices(fullName):
    idxs = []
    deviceHash = deviceId(fullName)
    for device in Devices:
        if Devices[device].DeviceID == deviceHash:
            idxs.append(device)

    Debug('tasmota::findDevices: fullName: {}, Idxs {}'.format(fullName, repr(idxs)))
    return idxs


# Collects a list of all supported attribute key/value pairs from tasmota tele STATE messages
def getStateDevices(message):
    states = []
    for attr in ['POWER', 'Heap', 'LoadAvg'] + ['POWER{}'.format(r) for r in range(1, 33)]:
        try:
            value = message[attr]
            states.append((attr, value))
        except:
            pass

    wifiattrs = ['RSSI']
    for attr in wifiattrs:
        try:
            value = message['Wifi'][attr]
            states.append((attr, value))
        except:
            pass
    return states


# Collects a list of all supported attribute sensor/type/value tuples from tasmota tele SENSOR messages
# * One sensor can contain several types (e.g. DHT11 has Temperature and Humidity)
# * Additional desc contains info needed to create a matching domoticz device
#  * Name is used for display / translation
#  * Unit is only relevant for DomoType Custom (AFAIK other types have fixed units in domoticz)
#  * Valid DomoType strings can be found in maptypename(): https://github.com/domoticz/domoticz/blob/development/hardware/plugins/PythonObjects.cpp#L365

#MQT: tele/D1-HAIER-POW/SENSOR = {"Time":"2024-11-12T23:01:05",
#"ENERGY":
# {"TotalStartTime":"2024-02-20T22:54:12",
# "Total":711.792,"TotalTariff":[96.965,614.826],
# "Yesterday":9.928,"Today":10.541,"Period":12,
# "Power":734,"ApparentPower":769,"ReactivePower":230,"Factor":0.95,
# "Voltage":242,"Current":3.182}}

#MQT: tele/D3-ELUX-POW/SENSOR = {"Time":"2024-11-12T23:03:50",
#"ENERGY":
#{"TotalStartTime":"2021-12-18T16:50:32",
# "Total":6821.566,
# "Yesterday":14.113,"Today":17.887,"Period":27,
# "Power":1666,"ApparentPower":1718,"ReactivePower":420,"Factor":0.97,
# "Voltage":234,"Current":7.345}}

#MQT: tele/D3-H12KW/SENSOR = {"Time":"2024-11-24T22:07:28",
#"ANALOG":
#{"CTEnergy1":{"Energy":62.962,"Power":2,"Voltage":220,"Current":0.010},
#"Range5":188},
#"DS18B20-1":{"Id":"3C01F09620CB","Temperature":38.2},
#"DS18B20-2":{"Id":"1DA15B1E64FF","Temperature":37.6},
#"TempUnit":"C"}


 
def getSensorDeviceState(states, sens, type, value):
    typeDb = {
    'Temperature':   {'Name': 'Temperature',   'Unit': '°C',   'DomoType': 'Temperature'},
    'Humidity':      {'Name': 'Humidity',      'Unit': '%',    'DomoType': 'Humidity'},
    'Pressure':      {'Name': 'Pressure',      'Unit': 'hPa',  'DomoType': 'Barometer'},
    'Illuminance':   {'Name': 'Illuminance',   'Unit': 'lux',  'DomoType': 'Illumination'},
    'Distance':      {'Name': 'Distance',      'Unit': 'mm ',  'DomoType': 'Distance'},
    'UvLevel':       {'Name': 'UV Level',      'Unit': 'raw',  'DomoType': 'Custom'},
    'UvIndex':       {'Name': 'UV Index',      'Unit': 'UVI',  'DomoType': 'Custom'},
    'UvPower':       {'Name': 'UV Power',      'Unit': 'W/m²', 'DomoType': 'Custom'},
    'Total':         {'Name': 'Total',         'Unit': 'kWh',  'DomoType': 'Custom'},
    'Yesterday':     {'Name': 'Yesterday',     'Unit': 'kWh',  'DomoType': 'Custom'},
    'Today':         {'Name': 'Today',         'Unit': 'kWh',  'DomoType': 'Custom'},
    'Power':         {'Name': 'Power',         'Unit': 'kW',   'DomoType': 'Usage'},
    'TotalTariff':   {'Name': 'Tariff',        'Unit': '',     'DomoType': '250;1;0'},
    'ApparentPower': {'Name': 'ApparentPower', 'Unit': 'kW',   'DomoType': 'Usage'},
    'ReactivePower': {'Name': 'ReactivePower', 'Unit': 'kW',   'DomoType': 'Usage'},
    'Factor':        {'Name': 'Factor',        'Unit': 'W/VA', 'DomoType': 'Custom'},
    'Frequency':     {'Name': 'Frequency',     'Unit': 'Hz',   'DomoType': 'Custom'},
    'Voltage':       {'Name': 'Voltage',       'Unit': 'V',    'DomoType': 'Voltage'},
    'Current':       {'Name': 'Current',       'Unit': 'A',    'DomoType': 'Current (Single)'},
    'Range':         {'Name': 'Pressure',      'Unit': 'Bar',  'DomoType': 'Pressure'}, #hack: Analog Range treat prassure
    'A':             {'Name': 'Pressure',      'Unit': 'Bar',  'DomoType': 'Pressure'}
#    P1 Smart Meter: 250,1,0 (hardware type)
#    P1 Smart Meter: 250,1,0 (hardware type)
#    'TotalTariff':   {'Name': 'TotalTariff',  'Unit': '',     'DomoType': '250;1;0'} #need to add Power value to sValue = "T1;T2;0.0;0.0,P,0";
#    'TotalTariff':   {'Name': 'TotalTariff',   'Unit': '',     'DomoType': 'P1 Smart Meter'}
    }

    if type in typeDb and value is not None:
        desc = typeDb[type].copy()
        desc['Sensor'] = sens
        if sens == 'ENERGY':
            desc['Sensor'] = 'Energy'
        states.append((sens, type, value, desc))

# Возвращает массив значеий сенсоров устройсва.
def getSensorDeviceStates(message):
    states = []
    if isinstance(message, collections.Mapping):
        for sensor, sensorData in message.items():
            if isinstance(sensorData, collections.Mapping):
                for type, value in sensorData.items():
                    if sensor == 'ANALOG' :
                        if isinstance(value, collections.Mapping):
                            for value, value1 in value.items():
                                getSensorDeviceState(states, type, value, value1)
                        else:
                            if(type.startswith('A')):
#                                v = float(value)/100
                                getSensorDeviceState(states,type,'A',value)
                            elif(type.startswith('Range')):
                                v = float(value)/100
                                getSensorDeviceState(states,type,'Range',v)
                    else:
                        getSensorDeviceState(states,sensor,type,value)
    return states


# Find the domoticz device unit id matching a STATE or SENSOR attribute coming from tasmota
def deviceByAttr(idxs, attr):
    for idx in idxs:
        try:
            description = json.loads(Devices[idx].Description)
            if description['Command'] == attr:
                return idx
        except:
            pass
    return None


# Some domoticz device Create(), Update() and query value examples
#
#  Domoticz.Device(Name=unitname, Unit=iUnit,TypeName="Switch",Used=1,DeviceID=unitname).Create()
#  Domoticz.Device(Name=unitname, Unit=iUnit,Type=243,Subtype=29,Used=1,DeviceID=unitname).Create()
#  Domoticz.Device(Name=unitname, Unit=iUnit,Type=244, Subtype=62, Switchtype=13,Used=1,DeviceID=unitname).Create() # create Blinds Percentage
#  Domoticz.Device(Name=unitname, Unit=iUnit,Type=244, Subtype=62, Switchtype=15,Used=1,DeviceID=unitname).Create() # create Venetian Blinds EU type
#  Domoticz.Device(Name=unitname+" BUTTON", Unit=iUnit,TypeName="Switch",Used=0,DeviceID=unitname).Create()
#  Domoticz.Device(Name=unitname+" LONGPUSH", Unit=iUnit,TypeName="Switch",Used=0,DeviceID=unitname).Create()
#  Domoticz.Device(Name=unitname, Unit=iUnit, TypeName="Temp+Hum",Used=1,DeviceID=unitname).Create() # create Temp+Hum Type=82
#
#  Devices[iUnit].Update(nValue=1, sValue="On")
#  Devices[iUnit].Update(nValue=0, sValue=str(curval), BatteryLevel=int(mval))
#
#  curval = Devices[iUnit].sValue
#  Domoticz.Device(Name=unitname, Unit=iUnit,Type=241, Subtype=3, Switchtype=7, Used=1,DeviceID=unitname).Create() # create Color White device
#  Domoticz.Device(Name=unitname, Unit=iUnit,Type=241, Subtype=6, Switchtype=7, Used=1,DeviceID=unitname).Create() # create RGBZW device


# Create a domoticz device from infos extracted out of tasmota STATE tele messages (POWER*)
def createStateDevice(fullName, cmndName, deviceAttr):
    '''
    Create domoticz device for deviceName
    DeviceID is hash of fullName
    Description contains necessary info as json (previously used Options, but got overwritten for Custom devices)
    '''

    for idx in range(1, 512):
        if idx not in Devices:
            break

    if deviceAttr in ['POWER'] + ['POWER{}'.format(r) for r in range(1, 33)]:
        deviceHash = deviceId(fullName)
        deviceName = '{} {}'.format(fullName, deviceAttr)
        description = {'Topic': cmndName, 'Command': deviceAttr, 'Device': 'Schalter'}
        if deviceAttr == 'POWER':
            description["Type"] = ""
        else:
            description["Type"] = deviceAttr[5:]
        Domoticz.Device(Name=deviceName, Unit=idx, TypeName="Switch", Used=1,
                        Description=json.dumps(description, indent=2, ensure_ascii=False), DeviceID=deviceHash).Create()
        if idx in Devices:
            # Remove hardware/plugin name from domoticz device name
            Devices[idx].Update(
                nValue=Devices[idx].nValue, sValue=Devices[idx].sValue, Name=deviceName, SuppressTriggers=True)
            Domoticz.Log("tasmota::createStateDevice: ID: {}, Name: {}, On: {}, Hash: {}".format(
                idx, deviceName, fullName, deviceHash))
            return idx
        Domoticz.Error("tasmota::createStateDevice: Failed creating Device ID: {}, Name: {}, On: {}".format(
            idx, deviceName, fullName))

    return None


# Create a domoticz device from infos extracted out of tasmota SENSOR tele messages
def createSensorDevice(fullName, cmndName, deviceAttr, desc):
    '''
    Create domoticz sensor device for deviceName
    DeviceID is hash of fullName
    Description contains necessary info as json (previously used Options, but got overwritten for Custom devices)
    '''

    for idx in range(1, 512):
        if idx not in Devices:
            break

    deviceHash = deviceId(fullName)
    deviceName = '{} {} {}'.format(fullName, desc['Sensor'], desc['Name'])
    description = {'Topic': cmndName, 'Command': deviceAttr,
                   'Device': desc['Sensor'], 'Type': desc['Name']}
    if desc['DomoType'] == 'Custom':
        options = {'Custom': '1;{}'.format(desc['Unit'])}
    else:
        options = None

    Domoticz.Log("tasmota::createSensorDevice1: Name: {}, On: {}, Hash: {}".format(
        deviceName, fullName, deviceHash))

    if not desc['DomoType'][:1].isdigit():
        # Create device that has a TypeName (prefered by domoticz)
        Domoticz.Device(Name=deviceName, Unit=idx, TypeName=desc['DomoType'], Used=1, Options=options,
            Description=json.dumps(description, indent=2, ensure_ascii=False), DeviceID=deviceHash).Create()
    else:
        # Create device without TypeName using domoticz low level Type, Subtype and Switchtype
        dtype, dsub, dswitch = desc['DomoType'].split(";")
        Domoticz.Device(Name=deviceName, Unit=idx, Type=int(dtype), Subtype=int(dsub), Switchtype=int(dswitch), Used=1, Options=options,
            Description=json.dumps(description, indent=2, ensure_ascii=False), DeviceID=deviceHash).Create()

    if idx in Devices:
        # Remove hardware/plugin name from domoticz device name
        Devices[idx].Update(
            nValue=Devices[idx].nValue, sValue=Devices[idx].sValue, Name=deviceName, SuppressTriggers=True)
        Domoticz.Log("tasmota::createSensorDevice2: ID: {}, Name: {}, On: {}, Hash: {}".format(
            idx, deviceName, fullName, deviceHash))
        return idx

    Domoticz.Error("tasmota::createSensorDevice: Failed creating Device ID: {}, Name: {}, On: {}".format(
        idx, deviceName, fullName))
    return None


# Translate device value received form domoticz to tasmota attribute/value
def d2t(attr, value):
    if attr in ['POWER'] + ['POWER{}'.format(r) for r in range(1, 33)]:
        if value == "On":
            return "on"
        elif value == "Off":
            return "off"
    return None


# Translate values of a tasmota attribute to matching domoticz device value
def t2d(attr, value, type, subtype):
    if attr in ['POWER'] + ['POWER{}'.format(r) for r in range(1, 33)]:
        if value == "ON":
            return 1, "On"
        elif value == "OFF":
            return 0, "Off"
    elif type == 81:
        # Domoticz humidity only accepted as integer
        return int(round(float(value))), "0"
    elif type == 243:
        if subtype == 26:
            # Domoticz barometer needs nValue=0 and sValue="pressure;5"
            return 0, "{};5".format(value)
        if subtype == 27:
            # Domoticz distance needs cm but gets mm
            return 0, str(float(value)/10)
    return 0, str(value)


# Update a tasmota attributes value in its associated domoticz device idx
def updateValue(idx, attr, value):
    nValue, sValue = t2d(attr, value, Devices[idx].Type, Devices[idx].SubType)
    if nValue != None and sValue != None:
        if Devices[idx].nValue != nValue or Devices[idx].sValue != sValue:
            Debug("tasmota::updateValue: Idx:{}, Attr: {}, nValue: {}, sValue: {}".format(
                idx, attr, nValue, sValue))
            Devices[idx].Update(nValue=nValue, sValue=sValue)


# Update domoticz device values related to tasmota STATE message (POWER*), create device if it does not exist yet
# Returns true if a new device was created
def updateStateDevices(fullName, cmndName, message):
    ret = False
    idxs = findDevices(fullName)
    for attr, value in getStateDevices(message):
        idx = deviceByAttr(idxs, attr)
        if idx == None:
            idx = createStateDevice(fullName, cmndName, attr)
            if idx != None:
                ret = True
        if idx != None:
            updateValue(idx, attr, value)
    return ret


# Update domoticz device related to tasmota RESULT message (e.g. on power on/off)
def updateResultDevice(fullName, message):
    idxs = findDevices(fullName)
    attr, value = next(iter(message.items()))
    for idx in idxs:
        description = json.loads(Devices[idx].Description)
        if description['Command'] == attr:
            updateValue(idx, attr, value)


# Update domoticz device values related to tasmota SENSOR message, create device if it does not exist yet
# Returns true if a new device was created
def updateSensorDevices(fullName, cmndName, message):
    ret = False
    idxs = findDevices(fullName)
    #   ENERGY, Voltage, 220 {Name: Voltage, Unit: V}
    Debug('tasmota::updateSensorDevices: message {}'.format(repr(message)))
    for sensor, type, value, desc in getSensorDeviceStates(message):
        attr = '{}-{}'.format(sensor, type)
        idx = deviceByAttr(idxs, attr)
        if idx == None:
            idx = createSensorDevice(fullName, cmndName, attr, desc)
            if idx != None:
                ret = True
        if idx != None:
            updateValue(idx, attr, value)
    return ret


# Update domoticz device description related to tasmota INFO1 message: Version and Module
def updateInfo1Devices(fullName, cmndName, message):
    try:
        module = message["Info1"]["Module"]
        version = message["Info1"]["Version"]
        for idx in findDevices(fullName):
            description = json.loads(Devices[idx].Description)
            dirty = False
            if "Module" not in description or module != description["Module"]:
                Domoticz.Log("tasmota::updateInfo1Devices: idx: {}, name: {}, module: {}".format(
                    idx, Devices[idx].Name, module))
                description["Module"] = module
                dirty = True
            if "Version" not in description or version != description["Version"]:
                Domoticz.Log("tasmota::updateInfo1Devices: idx: {}, name: {}, version: {}".format(
                    idx, Devices[idx].Name, version))
                description["Version"] = version
                dirty = True
            if dirty:
                Devices[idx].Update(nValue=Devices[idx].nValue, sValue=Devices[idx].sValue, 
                    Description=json.dumps(description, indent=2, ensure_ascii=False), SuppressTriggers=True)
    except Exception as e:
        Domoticz.Error("Exception:tasmota::updateInfo1Devices: Set module and version failed: {}".format(str(e)))


# Update domoticz device names and description from friendly names of tasmota STATUS message (seen on boot)
def updateStatusDevices(fullName, cmndName, message):
    try:
        names = message["Status"]["FriendlyName"]
        for idx in findDevices(fullName):
            description = json.loads(Devices[idx].Description)
            command = description["Command"]
            nonames = ['Sonoff', 'Tasmota', '', None] + ['Tasmota{}'.format(r) for r in range(2, 9)]
            name = None
            for i in range(1, 8):
                if command == "POWER{}".format(i+1) and len(names) > i and names[i] not in nonames:
                    name = names[i]
                    break
            if name == None and names[0] not in nonames:
                # not a multi power switch or multi value sensor: use first friendly name
                name = names[0]
            if name != None and command != 'POWER':
                # sensors names combine friendly name + type
                name += ' ' + description["Type"]
            # Check if name is valid and has changed
            if name != None and Devices[idx].Name != name and ('Name' not in description or Devices[idx].Name == description["Name"]):
                Domoticz.Log("tasmota::updateStatusDevices: idx: {}, from: {}, to: {}".format(
                    idx, Devices[idx].Name, name))
                description["Name"] = name
                Devices[idx].Update(
                    nValue=Devices[idx].nValue, sValue=Devices[idx].sValue, Name=name, 
                    Description=json.dumps(description, indent=2, ensure_ascii=False), SuppressTriggers=True)
            else:
                Debug("tasmota::updateStatusDevices: idx: {}, rename: {}, skipped: {}".format(
                    idx, Devices[idx].Name, repr(names)))
    except Exception as e:
        Domoticz.Error("Exception:tasmota::updateStatusDevices: Set friendly name failed: {}".format(str(e)))


# TODO
# Add or update tasmota network info in domoticz device description if it changed
def updateNetDevices(fullName, cmndName, message):
    pass


# TODO
# Handle tasmota ENERGY tele messages similar to SENSOR tele messages (still needed?)
def updateEnergyDevices(fullName, cmndName, message):
    pass

# TODO
# other types of switches (interlock, inching, shutters...)
# dimmers
# color control
# UI translations
# send RSSI on updates, RSSI as sensor value
# combined tasmota sensor values (temp/humi/baro, ...)
# respect units configured in tasmota (°C vs F, ...) 
