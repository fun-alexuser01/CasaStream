# Copyright 2013 Will Webberley.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# The full text of the License is available from 
# http://www.gnu.org/licenses/gpl.html



from flask import Flask, render_template
import os, nmap, subprocess, signal, urllib2, json, socket

app = Flask(__name__)
config_file = "config.json"
version = 1.0


# Get the address representing the master server on the local network. Requires an internet connection
def getAddress():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("example.com",80))
    ip_address = s.getsockname()[0]
    s.close()
    return ip_address

# Get the search stub of the address (i.e. '192.168.1.7' will return '192.168.1.')
def getAddressStub():
    ip_reversed = getAddress()[::-1]
    partitioned = ip_reversed.partition('.')[2]
    return partitioned[::-1]+"."
    

# Load the configuration from file
def getConfig():
    config = None
    try:
        config = open(config_file, "r").read()
        config = json.loads(config)
        pa_sink_id = int(config['pa_sink_id'])
        pa_rtp_id = int(config['pa_rtp_id'])
        vlc_pid = int(config['vlc_pid'])
    except Exception as e:
        print "Could not parse config file. You may need to restart the server. Writing new..."
        config = initConfig()
    return config

# Write a new configuration to file
def writeConfig(config):
    f = open(config_file, "w")
    f.write(json.dumps(config))
    f.close()

# Initialise the config file to initial starting values
def initConfig():
    config = {"pa_sink_id":-567,"pa_rtp_id":-567,"vlc_pid":-567, "enabled": 0}
    writeConfig(config)
    return config


# Save the PA module IDs and the VLC process ID to the config
def saveIds(mod1, mod2, pid):
    config = getConfig()
    config['pa_sink_id'] = int(mod1)
    config['pa_rtp_id'] = int(mod2)
    config['vlc_pid'] = int(pid)
    config['enabled'] = 1
    writeConfig(config)

# Get the PA module IDs and the VlC process ID from the config
def getIds():
    config = getConfig()
    return ((config['pa_sink_id'], config['pa_rtp_id'], config['vlc_pid']))
 
# Perform a scan over the network for slaves. Returns list of a dictionary of slaves
def search(stub):
    nm = nmap.PortScanner()
    nm.scan(hosts=stub, arguments='-p 9875')
   
    up_hosts = [] 
    for h in nm.all_hosts():
        if nm[h]['tcp'][9875]['state'] == 'open':
            up_hosts.append(str(h))

    zone_list = []
    for host in up_hosts:
        try:
            response = urllib2.urlopen('http://'+host+":"+str(9875)+"/info")
            zone = response.read()
            zone_info = json.loads(zone)
            zone_info['host'] = host
            zone_list.append(zone_info)
        except Exception as e:
            print "error:",host, e
    return zone_list

# Load the relevant PA modules to start the stream to localhost, and VLC to encode the stream to MP3.
def startStream():
    # Create a sink to receive our sound
    create_sink_process = subprocess.Popen(["pactl","load-module", "module-null-sink","sink_name=casastream","format=s16be","channels=2","rate=44100"], stdout=subprocess.PIPE)
    out, err = create_sink_process.communicate()    

    # Instruct sink to forward the stream to localhost for encoding
    create_rtp_process = subprocess.Popen(["pactl","load-module","module-rtp-send","source=casastream.monitor","destination=127.0.0.1","port=46998","loop=1"], stdout = subprocess.PIPE)
    out2, err2 = create_rtp_process.communicate()

    # Use VLC to encode stream to MP3 and then broadcast through RTP
    start_vlc_encoder = subprocess.Popen(['cvlc', 'rtp://@127.0.0.1:46998', ':sout=#transcode{acodec=mp3,ab=256,channels=2}:duplicate{dst=rtp{dst=225.0.0.1,mux=ts,port=12345}}'])
    pid = start_vlc_encoder.pid

    # Set initial volume to 90%:
    setMasterVolume(90)
        
    # Save PA module IDs and the VLC process ID (so they can be killed if CasaStream is disabled)
    saveIds(out, out2, pid)
        
# Unload the relevant modules and kill the VLC process to disable the server
def endStream():
    # Retrieve PA module IDs and the VLC process ID
    mod1, mod2, pid = getIds()
    
   # Kill PA modules
    subprocess.Popen(["pactl","unload-module",str(mod1)])
    subprocess.Popen(["pactl","unload-module",str(mod2)])

    # Kill VLC process
    os.kill(pid, signal.SIGTERM)
    
    initConfig() 

# Return an integer representing the ID of casastream's audio sink (if it exists)
def getCasaStreamSinkId():
    pa_sinks_process = subprocess.Popen(["pactl","list","short","sinks"], stdout = subprocess.PIPE)
    out1, err1 = pa_sinks_process.communicate()
    lines = out1.split("\n")
    sink_id = 0
    for line in lines:
        tokens = line.split()
        for token in tokens:
            if token == 'casastream':
                return int(tokens[0])
 

def getCasaStreamVolume():
    pa_sinks_process = subprocess.Popen(["pactl","list","sinks"], stdout = subprocess.PIPE)
    out1, err1 = pa_sinks_process.communicate()
    lines = out1.split("\n")
    casastream_found = False
    for line in lines:
        tokens = line.split()
        for token in tokens:
            if "casastream" in token:
                casastream_found = True
        if casastream_found == True and "Volume:" in line:
            volume = tokens[2]
            try:
                return int(volume.replace("%",""))
            except:
                continue
    return 90


# Set every input found to send sound to casastream's sink
def redirectAllInputs():
    # First, get the id of the casastream RTP sink
    sink_id = getCasaStreamSinkId()    

    # Next, get the IDs of all current inputs
    pa_inputs_process = subprocess.Popen(["pactl","list","short","sink-inputs"], stdout = subprocess.PIPE)
    out2, err2 = pa_inputs_process.communicate()
    lines = out2.split("\n")
    inputs = []
    for line in lines:
        tokens = line.split()
        if len(tokens) > 0:
            inputs.append(tokens[0])
    
    # Finally, move each input to our own sink
    for i in inputs:
        subprocess.Popen(["pactl","move-sink-input",str(i),str(sink_id)])


# Set every provided input ID to send sound to casastream's sink.
# Any input NOT provided will be redirected away from casastream.
def redirectInputs(inputs_to_redirect):
    # First, get the id of the casastream RTP sink
    sink_id = getCasaStreamSinkId()  

    # Get all inputs (not just those selected)
    all_inputs = getAllInputs()
    
    # Calculate which sink to send sources to which ARENT chosen:
    standard_sink = 0
    if sink_id == 0:
        standard_sink = 1    

    # Finally, move each requested input to our own sink and others to the standard_sink
    for input in all_inputs:
        id = input['id']
        if id in inputs_to_redirect:
            subprocess.Popen(["pactl","move-sink-input",str(input['id']),str(sink_id)])
        else:
            subprocess.Popen(["pactl","move-sink-input",str(input['id']),str(standard_sink)])


# Get a list of dictionaries representing information on each sound source input
def getAllInputs():
    pa_sinks_process = subprocess.Popen(["pactl","list","sink-inputs"], stdout = subprocess.PIPE)
    out1, err1 = pa_sinks_process.communicate()
    lines = out1.split("\n")
    inputs = []
    current_id = 1
    current_correct_sink = False
    sink_id = getCasaStreamSinkId()
    current_sink_id = 1
    current_volume = 0
    for line in lines:
        if "Sink Input" in line:
            tokens = line.split()
            for token in tokens:
                if "#" in token:            
                    current_id = int(token.replace("#",""))
        if "Volume:" in line:
            tokens = line.split()
            vol_str = tokens[2]
            current_volume = int(vol_str.replace("%",""))
        if "application.name" in line:
            tokens = line.split('"')
            name = tokens[1].replace('"','')
            if not "vlc" in name.lower():
                print name+"'s sink ID:",current_sink_id
                if current_sink_id == sink_id:
	                inputs.append({"id":current_id,"name":name,"casastream":True,"volume":current_volume})
                else:
                    inputs.append({"id":current_id,"name":name,"casastream":False,"volume":current_volume})
        if "Sink: " in line:
            tokens = line.split()
            current_sink_id = int(tokens[1])
            if int(tokens[1]) == sink_id:
                current_correct_sink = True
            else:
                current_correct_sink == False            

    return inputs
                
def removeAllCasastreamModules():
    pa_modules = subprocess.Popen(["pactl","list","short","modules"], stdout = subprocess.PIPE)
    out1, err1 = pa_modules.communicate()
    lines = out1.split("\n")
    for line in lines:
        tokens = line.split()
        if len(tokens) > 0:
            id = int(tokens[0])
            for token in tokens:
                if "casastream" in token:
                    subprocess.Popen(["pactl","unload-module",str(id)])

def setMasterVolume(volume):
    sink_id = getCasaStreamSinkId()
    subprocess.Popen(["pactl","set-sink-volume",str(sink_id),str(volume)+"%"])

def setSourceVolume(source_id, volume):
    subprocess.Popen(["pactl","set-sink-input-volume",str(source_id),str(volume)+"%"])
 
# Check if the system is enabled or not. Casastream is flagged as enabled when the 
# PA modules are loaded and the VLC encoder is activated.
def isEnabled():
    config = getConfig()
    if config['enabled'] == 1:
        return True
    return False


#
# ROUTING METHODS
#

@app.route("/")
def home():
    return render_template('home.html')

@app.route("/scan/", methods=["POST", "GET"])
def scan():
    zones = search(getAddressStub()+"0-30")
    return json.dumps(zones)

@app.route("/scan/<stub>/", methods=["POST", "GET"])
def scan_stub(stub):
    zones = search(stub)
    return json.dumps(zones)

@app.route("/start/", methods=["POST", "GET"])
def start():
    startStream()
    return json.dumps({"started":True})

@app.route("/stop/", methods=["POST", "GET"])
def stop():
    endStream()
    return json.dumps({'stopped':True})

@app.route("/status/", methods=["POST", "GET"])
def status():
    enabled = isEnabled()
    inputs = getAllInputs()
    address_stub = getAddressStub()
    address = getAddress()
    casastream_volume = getCasaStreamVolume()
    return json.dumps({'enabled':enabled, "inputs":inputs, "address":address,"stub":address_stub,"version":version,"casastream_volume":casastream_volume})

@app.route("/sort-inputs/")
def remove_inputs():
	input_list = []
	redirectInputs(input_list)
	return json.dumps({'success':'true'})

@app.route("/sort-inputs/<inputs>/", methods=["POST", "GET"])
def sort_inputs(inputs):
    tokens = inputs.split(",")
    input_list = []
    for token in tokens:
        try:
            input_list.append(int(token))
        # Exception thrown every time since inputs always ends with a ','
        except Exception:
            continue
    redirectInputs(input_list)
    return json.dumps({'success': 'true'})

@app.route("/master-volume/<volume>/")
def master_volume(volume):
    setMasterVolume(volume)
    return json.dumps({'success':'true'})

@app.route("/source-volume/<source>/<volume>/")
def source_volume(source, volume):
    setSourceVolume(source, volume)
    return json.dumps({'success':'true'})

@app.route("/enable-slave/<host>/")
def enable_slave(host):
    urllib2.urlopen('http://'+host+":"+str(9875)+"/start")
    return json.dumps({'success': 'true'})

@app.route("/disable-slave/<host>/")
def disable_slave(host):
    urllib2.urlopen('http://'+host+":"+str(9875)+"/stop")
    return json.dumps({'success': 'true'})

@app.route("/rename-slave/<host>/<name>/")
def rename_slave(host, name):
    urllib2.urlopen('http://'+host+":"+str(9875)+"/rename/"+name)
    return json.dumps({'success': 'true'})


# Main code (if invoked from Python at command line for development server)
if __name__ == '__main__':
    initConfig()
    removeAllCasastreamModules()
    app.debug = True 
    port = 9878
    app.run(host='0.0.0.0', port=port)
