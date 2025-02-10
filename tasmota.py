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
            topic = description['Topic']
#            topic = '{}/{}'.format(description['Topic'],
#                                   description['Command'])
        except:
            return False

        msg = d2t(description['Command'], Command)
        if msg is None:
            Debug("Handler::onDomoticzCommand: no message")
            return False

        try:
# Z2T message: zbsend {"device":"Plug1","send":{"power":0}}
            Debug("Handler::onDomoticzCommand: topic: {}, msg: {}".format(topic, msg))
            self.mqttClient.publish(topic, msg)
        except Exception as e:
            Domoticz.Error("Handler::onDomoticzCommand: {}".format(str(e)))
            return False

        return True

    # Subscribe to our topics 
    # Z2T should use SetOption89 0 to subscribe short form: %prfix%/%topic%/%tail% (tele/D1-Z2T-TEST1/SENSOR)
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
        # Debug("Handler::onMQTTPublish: topic: {}, self.topics: {}, self.subscriptions: {}".format(topic, self.topics, self.subscriptions))
        # self.topics: 'INFO1', 'STATE', 'SENSOR', 'RESULT', 'STATUS', 'STATUS5', 'STATUS8', 'STATUS11', 'ENERGY'
        # self.subscriptions: ['%prefix%/%topic%', '%topic%/%prefix%'] 
        # Check if we handle this topic tail at all (hardcoded list SENSOR, STATUS, ...)
        subtopics = topic.split('/')
        tail = subtopics[-1]

        if subtopics[1] not in self.tasmotaDevices:
            return True

        if tail not in self.topics:
            return True

        Debug("Handler::onMQTTPublish: tail in self.tasmotaDevices: tail: {}, topic: {}".format(
            tail, str(topic)))

        # Different Tasmota devices can have different FullTopic patterns.
        # All FullTopic patterns we care about are in self.subscriptions (plugin config)
        # Tasmota devices will be identified by a hex hash from FullTopic without %prefix%

        # Identify the subscription that matches our received subtopics
        fulltopic = []
        cmndtopic = []
        for subscription in self.subscriptions:
            patterns = subscription.split('/')
            for subtopic, pattern in zip(subtopics[:-1], patterns):
                Debug("Handler::onMQTTPublish: for loop subtopic: {}, pattern: {}, message: {}".format(
                    subtopic, pattern, str(message)))
                if((pattern not in ('%topic%', '%prefix%', '+', subtopic)) or
                    (pattern == '%prefix%' and subtopic != self.prefix[2] and subtopic != self.prefix[3]) or
                        (pattern == '%topic%' and (subtopic == 'sonoff' or subtopic == 'tasmota'))):
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
        cmnd = '/'.join(cmndtopic)


        # fullName should now contain all subtopic parts except for %prefix%es and tail
        # I.e. fullName is uniquely identifying the sensor or button referred by the message

        if fullName not in self.tasmotaDevices:
            return True

        Debug("Handler::onMQTTPublish: device: {}, cmnd: {}, tail: {}, message: {}".format(
            fullName, cmnd, tail, str(message)))
        if tail == 'STATE':  # POWER* status
            if updateStateDevices(fullName, cmnd, message):
                self.requestStatus(cmnd)
        elif tail == 'SENSOR':
            if updateSensorDevices(fullName, cmnd, message):
                self.requestStatus(cmnd)
        elif tail == 'RESULT':  # POWER* change
            updateResultDevice(fullName, message)
        elif tail == 'STATUS':  # Friendly names
            updateStatusDevices(fullName, cmnd, message)
        elif tail == 'INFO1':  # update module and version in device description
            updateInfo1Devices(fullName, cmnd, message)
            self.requestStatus(cmnd)
        elif tail == 'STATUS5':  # nop
            updateNetDevices(fullName, cmnd, message)
        elif tail == 'ENERGY':  # nop
            updateEnergyDevices(fullName, cmnd, message)

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

 #   Debug('tasmota::findDevices: fullName: {}, Idxs {}'.format(fullName, repr(idxs)))
    return idxs

def getDeviceHash(fullName,z2t):
    deviceHash = deviceId(fullName)
    if z2t:
        deviceHash = '{}-Z2T'.format(deviceHash) 
    return deviceHash  

def findDevicesByHash(deviceHash):
    idxs = []
    for device in Devices:
        if Devices[device].DeviceID == deviceHash:
            idxs.append(device)
 #   Debug('tasmota::findZigbeeDevices: fullName: {}, Idxs {}'.format(fullName, repr(idxs)))
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
#  * Valid DomoType strings can be found in maptypename(): https://github.com/domoticz/domoticz/blob/development/hardware/plugins/PythonObjects.cpp#L371
#  * If there is no DomoType TypeName matching the sensor type, use a tuple of domoticz Type;Subtype;Switchtype

#MQT: tele/D1-HAIER-POW/SENSOR = {"Time":"2024-11-12T23:01:05",
#"ENERGY":
# {"TotalStartTime":"2024-02-20T22:54:12",
# "Total":711.792,"TotalTariff":[96.965,614.826], "Yesterday":9.928,"Today":10.541,"Period":12, "Power":734,"ApparentPower":769,"ReactivePower":230,"Factor":0.95, "Voltage":242,"Current":3.182}}

#MQT: tele/D3-H12KW/SENSOR = {"Time":"2024-11-24T22:07:28",
#"ANALOG": {"CTEnergy1":{"Energy":62.962,"Power":2,"Voltage":220,"Current":0.010}, "Range5":188},
#"DS18B20-1":{"Id":"3C01F09620CB","Temperature":38.2},
#"DS18B20-2":{"Id":"1DA15B1E64FF","Temperature":37.6},
#"TempUnit":"C"}

#2024-11-28 23:11:34.258 Д1-TasmotaAD: Handler::onMQTTPublish: device: D1-HAIER-POW, cmnd: cmnd/D1-HAIER-POW, tail: SENSOR, 
#message: {'Time': '2024-11-28T23:11:33', 'ENERGY': {'TotalStartTime': '2024-02-20T22:54:12', 'Total': 884.774, 'TotalTariff': [42.824, 841.95],
#'Yesterday': 23.144, 'Today': 19.221, 'Period': 11, 'Power': 662, 'ApparentPower': 693, 'ReactivePower': 204, 'Factor': 0.96, 'Voltage': 232, 'Current': 2.984}}

# https://www.b4x.com/android/forum/threads/rrfxmeter.139786/
 
def getSensorDeviceState(states, sensName, attr, value, linkQuality):
    typeDb = {
    'Temperature':   {'Name': 'Temperature',   'Unit': '°C',   'DomoType': 'Temperature'},
    'Humidity':      {'Name': 'Humidity',      'Unit': '%',    'DomoType': 'Humidity'},
    'Temp+Hum':      {'Name': 'Temp+Hum',      'Unit': '',     'DomoType': 'Temp+Hum'}, # combine Temp+Hum device if Temperature and Humidity exist in attr list
    'Pressure':      {'Name': 'Pressure',      'Unit': 'hPa',  'DomoType': 'Barometer'},
    'Illuminance':   {'Name': 'Illuminance',   'Unit': 'lux',  'DomoType': 'Illumination'},
    'Distance':      {'Name': 'Distance',      'Unit': 'mm ',  'DomoType': 'Distance'},
    'UvLevel':       {'Name': 'UV Level',      'Unit': 'raw',  'DomoType': 'Custom'},
    'UvIndex':       {'Name': 'UV Index',      'Unit': 'UVI',  'DomoType': 'Custom'},
    'UvPower':       {'Name': 'UV Power',      'Unit': 'W/m²', 'DomoType': 'Custom'},
    'Total':         {'Name': 'Total',         'Unit': 'kWh',  'DomoType': '113;0;0'}, #0x71, ??? pTypeRFXMeter
    'TotalTariff':   {'Name': 'P1 meter',      'Unit': '',     'DomoType': '250;1;0'}, #pTypeP1Power,sTypeP1Power
    'Yesterday':     {'Name': 'Yesterday',     'Unit': 'kWh',  'DomoType': 'Custom'},
    'Today':         {'Name': 'Today',         'Unit': 'kWh',  'DomoType': 'Custom'},
    'Power':         {'Name': 'Power',         'Unit': 'kW',   'DomoType': 'Usage'},
    'ApparentPower': {'Name': 'ApparentPower', 'Unit': 'kW',   'DomoType': 'Usage'},
    'ReactivePower': {'Name': 'ReactivePower', 'Unit': 'kW',   'DomoType': 'Usage'},
    'Factor':        {'Name': 'Factor',        'Unit': 'W/VA', 'DomoType': 'Custom'},
    'Frequency':     {'Name': 'Frequency',     'Unit': 'Hz',   'DomoType': 'Custom'},
    'Voltage':       {'Name': 'Voltage',       'Unit': 'V',    'DomoType': 'Voltage'},
    'Current':       {'Name': 'Current',       'Unit': 'A',    'DomoType': 'Current (Single)'},
    'Range':         {'Name': 'Pressure',      'Unit': 'Bar',  'DomoType': 'Pressure'}  #hack: Analog Range treat pressure
#    P1 Smart Meter: 250,1,0 (hardware type)
#    P1 Smart Meter: 250,1,0 (hardware type)
#    'TotalTariff':   {'Name': 'TotalTariff',  'Unit': '',     'DomoType': '250;1;0'} #need to add Power value to sValue = "T1;T2;0.0;0.0,P,0";
#    'TotalTariff':   {'Name': 'TotalTariff',  'Unit': '',     'DomoType': 'P1 Smart Meter'}
    }

    if attr in typeDb and value is not None:
        desc = typeDb[attr].copy()
        desc['Sensor'] = sensName
        desc['LinkQuality'] = linkQuality
#        if sens == 'ENERGY':
#            desc['Sensor'] = 'Energy'
        desc['LinkQuality'] = None
        states.append((sensName, attr, value, desc))

#combine Sensor Attributes before
def getComposeAttr(attrList):
    isTemp = False
    isHum = False
    linkQuality = None
    composeAttr = None
    composeValue = None
    sensorName = None
    for Attr, Value in attrList.items():
        if Attr == 'Name':
            sensorName = Value
        if Attr == 'Temperature':
            isTemp = True
            Temp = Value
        if Attr == 'Humidity':
            isHum = True
            Hum = Value
        if Attr == 'Pressure':
            isPress = True
            Press = Value
        if Attr == 'LinkQuality':
            linkQuality = round(float(int(Value) / 20))

    if isTemp and isHum:
        composeAttr = 'Temp+Hum'
        composeValue = "{};{};1".format(Temp,int(round(float(Hum)))) # Domoticz humidity only accepted as integer 
    return sensorName,composeAttr,composeValue,linkQuality

def getSensorDeviceStateEx(states, sensorName, attrList):
    dummyName,Attr,Value,linkQuality = getComposeAttr(attrList)
    Debug('tasmota::getSensorDeviceStatesEx: sensorName: {}, Attr: {}, Value: {}'.format(sensorName, Attr, Value))
    if Attr == None: # not compose attribute
        for Attr, Value in attrList.items():
            getSensorDeviceState(states, sensorName, Attr, Value, linkQuality)
    else:
        getSensorDeviceState(states, sensorName, Attr, Value, linkQuality)

def getZigbeeDeviceState(states, sensName, attr, value, linkQuality):
    typeDb = {
    'Temperature':        {'Name': 'Temperature',   'Unit': '°C',   'DomoType': 'Temperature'},
    'Humidity':           {'Name': 'Humidity',      'Unit': '%',    'DomoType': 'Humidity'},
    'Temp+Hum':           {'Name': 'Temp+Hum',      'Unit': '',     'DomoType': 'Temp+Hum'}, # combine Temp+Hum device if Temperature and Humidity exist in attr list
    'ZoneStatusChange':   {'Name': 'Alert',         'Unit': '',     'DomoType': 'Alert'},    # Water sensor
    'ZoneStatus':         {'Name': 'Alert',         'Unit': '',     'DomoType': 'Alert'},    # Motion sensor
    'Contact':            {'Name': 'Alert',         'Unit': '',     'DomoType': 'Alert'},    # Door sensor
    'Power':              {'Name': 'Switch',        'Unit': '',     'DomoType': 'Switch'}    # Switch
    }

    if attr in typeDb and value is not None:
        desc = typeDb[attr].copy()
        desc['Sensor'] = sensName
        desc['LinkQuality'] = linkQuality
        states.append((sensName, attr, value, desc))


def getZigbeeDeviceStateEx(states, attrList):
    sensorName,Attr,Value,linkQuality = getComposeAttr(attrList)
    Debug('tasmota::getZigbeeDeviceStateEx: sensorName: {}, Attr: {}, Value: {}'.format(sensorName, Attr, Value))
    if Attr == None:
        for Attr, Value in attrList.items():
            getZigbeeDeviceState(states, sensorName, Attr, Value, linkQuality)
    else:
        getZigbeeDeviceState(states, sensorName, Attr, Value, linkQuality)

# Возвращает массив значеий сенсоров устройсва.
def getSensorDeviceStates(sensorName, sensorData):
    states = []
    if sensorName == 'ZbReceived': #zigbee2tasmota sensor
        Debug('tasmota::getSensorDeviceStates:ZbReceived: sensorName: {}, sensorData: {}'.format(sensorName, sensorData))
        if isinstance(sensorData, collections.Mapping):
            for deviceName, AttrList in sensorData.items(): # deviceName skipped, used from sensorData
                if isinstance(AttrList, collections.Mapping):
                    getZigbeeDeviceStateEx(states, AttrList)
        else: # no device name in message (SetOption83 1 ???) sensorData is attribute list
            getZigbeeDeviceStateEx(states, sensorData)
    elif sensorName == 'ANALOG':
        Debug('tasmota::getSensorDeviceStates:ANALOG: sensorName: {}, sensorData: {}'.format(sensorName, sensorData))
        if isinstance(sensorData, collections.Mapping):
            for sensor, value in sensorData.items():
                if sensor.startswith('CTEnergy') and isinstance(value, collections.Mapping):
#                  sensor: "CTEnergy1, CTEnergy2..."
                    getSensorDeviceStateEx(states,sensor,value)
                else: 
                    if sensor.startswith('Range'): # Range1,Range2 ...
                        value = float(value)/100
                        getSensorDeviceState(states,sensor,'Range',value,None)
                    elif(sensor.startswith('A')): #Analog A1,A2 ...
                        getSensorDeviceState(states,sensor,'Range',value)
                    elif(sensor.startswith('Temperature')): 
                        getSensorDeviceState(states,sensor,'Temperature',value,None)
                    elif(sensor.startswith('Light')):
                        getSensorDeviceState(states,sensor,'Illuminance',value,None)
    else: #all others sensors
        Debug('tasmota::getSensorDeviceStates:OTHER: sensorName: {}, sensorData: {}'.format(sensorName, sensorData))
        if isinstance(sensorData, collections.Mapping):
            getSensorDeviceStateEx(states,sensorName,sensorData)
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
def createStateDevice(fullName, cmnd, deviceAttr):
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

        cmnd = "{}/{}".format(cmnd,deviceAttr)

        description = {'Topic': cmnd, 'Command': deviceAttr, 'Device': 'Switch'}
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
        Domoticz.Error("tasmota::createStateDevice: Failed creating Device ID: {}, deviceName: {}, fullName: {}".format(
            idx, deviceName, fullName))

    return None


# Create a domoticz device from infos extracted out of tasmota SENSOR tele messages
def createSensorDevice(fullName, deviceHash, cmnd, deviceAttr, desc):

#    Create domoticz sensor device for device with fullName
#    DeviceID is hash of fullName, Zigbee DeviceID is Z2T-<hash of fullName>
#    Description contains necessary info as json to send  DomoticzCommand

    for idx in range(1, 512):
        if idx not in Devices:
            break

    attrs = deviceAttr.split('-')

    if len(attrs) > 2:
        deviceName = '{} {} {} {}'.format(fullName, desc['Sensor'], attrs[-2], desc['Name'])
    else:
        deviceName = '{} {} {}'.format(fullName, desc['Sensor'], desc['Name'])

    if len(attrs) > 1: #zigbee device type
        attr = attrs[-1]
        if attr.upper() == 'POWER':
            cmnd = "{}/{}".format(cmnd,'ZbSend')

    description = {'Topic': cmnd, 'Command': deviceAttr,
                   'Device': desc['Sensor'], 'Type': desc['Name']}

    if desc['DomoType'][0] == 'Custom':
        options = {'Custom': '1;{}'.format(desc['Unit'])}
    else:
        options = None

    Domoticz.Log("tasmota::createSensorDevice: deviceName: {}, fullName: {}, deviceHash: {}".format(
        deviceName, fullName, deviceHash))

    if not desc['DomoType'][:1].isdigit():
        # Create device by string TypeName
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
        Domoticz.Log("tasmota::createSensorDevice: ID: {}, Name: {}, On: {}, Hash: {}, Type: {}".format(
            idx, deviceName, fullName, deviceHash, desc['DomoType']))
        return idx

    Domoticz.Error("tasmota::createSensorDevice: Failed creating Device ID: {}, deviceName: {}, fullName: {}, deviceHash: {}, Type: {}".format(
        idx, deviceName, fullName, deviceHash, desc['DomoType']))
    return None

# Translate device value received form domoticz to tasmota attribute/value
def d2t(attr, value):
    attrs = attr.split('-')
    if len(attrs) > 1 : #command to sensor device(zigbee2tasmots switch) formst: deviceName-Power
# {"Device":"0x1234","Send":{"Power":0}}
# {"Device":"Switch1","Send":{"Power":0}}
        name = attrs[0]
        attr = attrs[-1]
#        index = attr.rfind(attr)
#        name = attr[:index-1]
        if attr.upper() == 'POWER':
            msg = {}
            msg['Device'] = name
            power = {}
            if value == "On":
                power['Power'] = 1
            elif value == "Off":
                power['Power'] = 0
            msg['Send'] = power
            return json.dumps(msg)

    if attr.upper() in ['POWER'] + ['POWER{}'.format(r) for r in range(1, 33)]:
        if value == "On":
            return "on"
        elif value == "Off":
            return "off"
    return None

# Translate values of a tasmota attribute to matching domoticz device value
# result: nValue, sValue
def t2d(idx, attr, value):
    type = Devices[idx].Type
    subtype = Devices[idx].SubType

    if attr.upper() in ['POWER'] + ['POWER{}'.format(r) for r in range(1, 33)]:
        if value == "ON" or value == 1 :
            return 1, "On"
        elif value == "OFF" or value == 0:
            return 0, "Off"
    elif type == 0x52: #'Temp+Hum'
        sValue = Devices[idx].sValue
        sValues = sValue.split(';')
        if attr == 'Temperature':
            Debug("tasmota::t2d:Temperature: sValue: {}, sValues: {}".format(sValue,sValues))
            return 0, "{};{};{}".format(value,sValues[1],sValues[2])
        if attr == 'Humidity':
            Debug("tasmota::t2d:Humidity: sValue: {}, sValues: {}".format(sValue,sValues))
            return 0, "{};{};{}".format(sValues[0],int(round(float(value))),sValues[2])
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
    elif type == 250: # Domoticz P1 meter
        # Domoticz P1 meter needs nValue=0 and sValue="T1;T2;0.0;0.0,P,0"
        return 0, "{};{};0.0;0.0;{};0".format(value[0]*1000,value[1]*1000,value[2])
    elif type == 113 and subtype in [0, 1, 2, 4]:
        # Energy, water and gas counters expected in Wh or l but come in as kWh or m³
        value = value * 1000

    return 0, str(value)

# Update a tasmota attributes value in its associated domoticz device idx
def updateValue(idx, attr, value, signalLevel):
    nValue, sValue = t2d(idx, attr, value)
    if nValue != None and sValue != None:
        if Devices[idx].nValue != nValue or Devices[idx].sValue != sValue:
            Debug("tasmota::updateValue: Idx:{}, Attr: {}, nValue: {}, sValue: {}, signalLevel: {}".format(
                idx, attr, nValue, sValue, signalLevel))
            if signalLevel != None:
                Devices[idx].Update(nValue=nValue, sValue=sValue, SignalLevel=signalLevel)
            else:
                Devices[idx].Update(nValue=nValue, sValue=sValue)

#Devices[idx].Update(nValue=nValue, sValue=sValue, SignalLevel=10, BatteryLevel=100) # SignalLevel=0-10 BatteryLevel=0-100

# Update domoticz device values related to tasmota STATE message (POWER*), create device if it does not exist yet
# Returns true if a new device was created
def updateStateDevices(fullName, cmndName, message):
    ret = False
    idxs = findDevices(fullName)
    Debug('tasmota::updateStateDevices: message {}'.format(repr(message)))
    for attr, value in getStateDevices(message):
        idx = deviceByAttr(idxs, attr)
        if idx == None:
            idx = createStateDevice(fullName, cmndName, attr)
            if idx != None:
                ret = True
        if idx != None:
            updateValue(idx, attr, value, None)
    return ret


# Update domoticz device related to tasmota RESULT message (e.g. on power on/off)
def updateResultDevice(fullName, message):
    idxs = findDevices(fullName)
    attr, value = next(iter(message.items()))
    Debug('tasmota::updateResultDevices: message {}'.format(repr(message)))
    for idx in idxs:
        try:
            description = json.loads(Devices[idx].Description)
            if description['Command'] == attr:
                updateValue(idx, attr, value, None)
        except Exception as e:
            Domoticz.Error("tasmota::updateResultDevice: Update value for idx {} failed: {}".format(idx, str(e)))


def deviceByNameType(idxs, deviceName, attrName):
    for idx in idxs:
        try:
            description = json.loads(Devices[idx].Description)
            if description['Device'] == deviceName:
                if description['Type'] == attrName:
                    return idx
                elif (attrName == 'Temperature' or attrName == 'Humidity') and description['Type'] == 'Temp+Hum' :
                    return idx
        except:
            pass
    return None


# Update domoticz device values related to tasmota SENSOR message, create device if it does not exist yet
# Returns true if a new device was created
def updateSensorDevices(fullName, cmnd, message):
    ret = False
    z2t = False
    if isinstance(message, collections.Mapping):
        for sensorName, sensorData in message.items():
            Debug('tasmota::updateSensorDevices: sensorName: {}, sensorData: {}'.format(sensorName, sensorData))
            if sensorName == 'ZbReceived':
                z2t = True

            deviceHash = getDeviceHash(fullName,z2t)
            idxs = findDevicesByHash(deviceHash)

            for sensor, attr, value, desc in getSensorDeviceStates(sensorName,sensorData):
                Debug('tasmota::updateSensorDevices: sensor {}, type {}, value {}, desc {}'.format(sensor, attr, value, desc))
                unicAttr = '{}-{}'.format(sensor, attr) #unicAttr = <Device name>-<attribute>
                idx = deviceByNameType(idxs, sensor, desc['Name'])
                if idx == None:
                    idx = createSensorDevice(fullName, deviceHash, cmnd, unicAttr, desc)
                    if idx != None:
                        ret = True
                if idx != None:
                    if desc['Name'] == 'P1 meter': #TotalTariff attribute found, need to add Power to value list
                        for sensor, attr1, value1, desc in getSensorDeviceStates(sensorName,sensorData):
                            if desc['Name'] == 'Power':
                                value.append(value1)
                                updateValue(idx, attr, value, desc['LinkQuality'])
                                break
                    else:
                        updateValue(idx, attr, value, desc['LinkQuality'])

                    if 'LinkQuality' in desc:
                        Debug('tasmota::updateSensorDevices: LinkQuality: {}'.format(desc['LinkQuality']))
#                        Devices[idx].Update(SignalLevel=desc['LinkQuality'])

                    if 'BatteryLevel' in desc:
                        Debug('tasmota::updateSensorDevices: BatteryLevel: {}'.format(desc['BatteryLevel']))
#                        Devices[idx].Update(BatteryLevel=desc['BatteryLevel'])
    return ret


# Update domoticz device description related to tasmota INFO1 message: Version and Module
def updateInfo1Devices(fullName, cmndName, message):
    Debug('tasmota::updateInfo1Devices: message {}'.format(repr(message)))
    try:
        if "Info1" in message:
            module = message["Info1"]["Module"]
            version = message["Info1"]["Version"]
        else:
            module = message["Module"]
            version = message["Version"]

        for idx in findDevices(fullName):
            try:
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
                Domoticz.Error("Exception:tasmota::updateInfo1Devices: Set module and version for idx {} failed: {}".format(idx, str(e)))

    except Exception as e:
        Domoticz.Error("Exception:tasmota::updateInfo1Devices: Get module and version failed: {}".format(str(e)))


# Update domoticz device names and description from friendly names of tasmota STATUS message (seen on boot)
def updateStatusDevices(fullName, cmndName, message):
    Debug('tasmota::updateStatusDevices: message {}'.format(repr(message)))
    try:
        names = message["Status"]["FriendlyName"]

        for idx in findDevices(fullName):
            try:
                description = json.loads(Devices[idx].Description)
                command = description["Command"]
                nonames = ['Sonoff', 'Tasmota', '', None] + ['Tasmota{}'.format(r) for r in range(2, 9)]
                name = None
                # check if device is one of several power switches (e.g. power[12] or one of sensors with multiple values (e.g. ENERGY-[12]-Current)
                for i in range(8):
                    if len(names) > i and names[i] not in nonames:
                        if command == "POWER{}".format(i+1):
                            name = names[i]
                            break
                        cmd = command.split('-')
                        if len(cmd) > 2 and cmd[-2] == str(i+1): 
                            name = names[i]
                            break
                if name == None and names[0] not in nonames:
                    # not a multi power switch or multi value sensor: use first friendly name
                    name = names[0]
                if name is not None and command != 'POWER':
                    # sensors names combine friendly name + type
                    name += ' ' + description["Type"]
                # Check if name is valid and has changed
                if name is not None and Devices[idx].Name != name and ('Name' not in description or Devices[idx].Name == description["Name"]):
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
                Domoticz.Error("Exception:tasmota::updateStatusDevices: Set friendly name for idx {} failed: {}".format(idx, str(e)))

    except Exception as e:
        Domoticz.Error("Exception:tasmota::updateStatusDevices: Get friendly name failed: {}".format(str(e)))


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
