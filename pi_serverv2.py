#!/usr/bin/env python

"""
This sample application is a server that supports COV notification services.
The console accepts commands that change the properties of an object that
triggers the notifications.
"""

import time
from threading import Thread,Event,Lock
from bacpypes.service.device import WhoIsIAmServices
from bacpypes.apdu import WhoIsRequest, IAmRequest
from bacpypes.debugging import bacpypes_debugging, ModuleLogger
from bacpypes.consolelogging import ConfigArgumentParser
from bacpypes.consolecmd import ConsoleCmd
from bacpypes.pdu import Address

from bacpypes.core import run, deferred, enable_sleeping
from bacpypes.task import RecurringTask
import logging

from bacpypes.app import BIPSimpleApplication
from bacpypes.primitivedata import Real
from bacpypes.object import (
    WritableProperty,
    AnalogValueObject,
    BinaryValueObject,
    register_object_type,
)    
from bacpypes.local.device import LocalDeviceObject
from bacpypes.service.cov import ChangeOfValueServices
logging.basicConfig(level=logging.DEBUG)
from bacpypes.primitivedata import Enumerated

from hubLoop import *
from flask import Flask, render_template, request, redirect, url_for, flash,jsonify

import json
import os

import signal
import sys

def signal_handler(sig, frame):
    """Handle termination signals and perform cleanup."""
    global test_application

    print("\nTermination signal received. Cleaning up...")
    
    # Stop the BACnet application
    if test_application:
        test_application.close_socket()
        test_application = None

    # Set the stop_event to terminate threads
    stop_event.set()

    # Exit the application
    sys.exit(0)

# Global BACnet variables
test_application = None
num_valves = 0  # Global variable to store the number of valves

CONFIG_FILE = "config.json"  # File to store the configuration

communication_log = []  # Stores logs of communication
log_lock = Lock()  # Lock to handle thread-safe updates

# test globals
test_av = None
test_bv = None

object_to_ids_mapping = {}  # Maps objectName to ids_list index

stop_event = Event()

# TWIG data
twig_gateway = None
valves = {
    1: {"status": "Closed","twig_id":0,"valve_number":0},
    2: {"status": "Closed","twig_id":0,"valve_number":0},
    3: {"status": "Closed","twig_id":0,"valve_number":0},
    4: {"status": "Closed","twig_id":0,"valve_number":0},
}

############################################## Memory settings #########################################################

def load_config():
    """Load configuration from a file."""
    global num_valves, object_to_ids_mapping
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            num_valves = config.get("num_valves", 0)
            object_to_ids_mapping = config.get("object_to_ids_mapping", {})
    else:
        num_valves = 0  # Default value if the file doesn't exist
        object_to_ids_mapping = {}

def save_config():
    """Save the current configuration to a file."""
    global num_valves, object_to_ids_mapping
    with open(CONFIG_FILE, "w") as f:
        json.dump({
            "num_valves": num_valves,
            "object_to_ids_mapping": object_to_ids_mapping
        }, f)

############################################## Web interface #########################################################
# Flask app setup
app = Flask(__name__)
app.secret_key = "super_secret_key"
@app.route('/')
def index():
    """Dashboard displaying valve status and controls."""
    global valves
    global object_to_ids_mapping
    event_object = get_event_loop()
    ids_list = list(event_object.unique_ids)
    i = 1
    for index , items in enumerate(ids_list):
        valves[index+i]['twig_id'] = items
        valves[index+i]["valve_number"] = 1
        valves[index+i+1]['twig_id'] = items
        valves[index+i+1]["valve_number"] = 2
    return render_template('index.html', 
                           object_to_ids_mapping=object_to_ids_mapping, 
                           valves=valves)
@app.route('/debug')
def debug():
    """Display communication logs."""
    event_object = get_event_loop()
    log_list = (event_object.communication_log)
    return render_template('debug.html', logs=log_list)
@app.route('/get_logs')
def get_logs():
    """Return the communication logs as JSON."""
    event_object = get_event_loop()
    log_list = (event_object.communication_log)
    return jsonify(log_list)
@app.route('/configure', methods=['POST'])
def configure():
    """Configure the number of valves and TWIG gateway."""
    global twig_gateway, valves,num_valves
    try:
        num_valves = int(request.form['num_valves'])
        save_config()
        # twig_gateway = request.form['gateway']
        valves = {i: {"status": "Unknown"} for i in range(1, num_valves + 1)}
        flash(f"Configured {num_valves} valves with gateway {twig_gateway}.", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/start-service', methods=['POST'])
def start_service():
    """Start BACnet/TWIG services."""
    global stop_event
    if not stop_event.is_set():
        flash("Service is already running!", "info")
        return redirect(url_for('index'))
    try:
        stop_event.clear()  # Reset stop signal
        thread = Thread(target=main)  # Start the main BACnet service in a new thread
        thread.daemon = True
        thread.start()
        flash("Service started successfully!", "success")
    except Exception as e:
        flash(f"Error starting service: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/stop-service', methods=['POST'])
def stop_service():
    """Stop BACnet/TWIG services."""
    global stop_event, test_application
    if stop_event.is_set():
        flash("Service is already stopped!", "info")
        return redirect(url_for('index'))
    try:
        stop_event.set()  # Signal threads to stop
        # Wait a moment for threads to terminate
        time.sleep(1)
        # Cleanup BACnet application if needed
        if test_application:
            test_application.close_socket()
            test_application = None 
        flash("Service stopped successfully!", "success")
    except Exception as e:
        flash(f"Error stopping service: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/map_object', methods=['POST'])
def map_object():
    """Map a BACnet object to an ids_list index."""
    global object_to_ids_mapping

    try:
        object_name = request.form['object_name']
        valve_index = int(request.form['valve_index'])
        
        # Update the mapping
        object_to_ids_mapping[object_name] = valve_index
        save_config()  # Save the mapping and num_valves after updating

        flash(f"Mapped object {object_name} to valve index {valve_index}.", "success")
    except Exception as e:
        flash(f"Error mapping object: {e}", "danger")

    return redirect(url_for('index'))



@app.route('/set-ip', methods=['POST'])
def set_ip():
    """Set a fixed IP address for the Raspberry Pi."""
    try:
        ip_address = request.form['ip_address']

        # Call the helper script to update the IP address
        script_path = os.path.join(os.path.dirname(__file__), 'set_static_ip.sh')
        command = f"sudo {script_path} {ip_address}"
        result = os.system(command)

        if result == 0:
            flash(f"IP address {ip_address} set successfully!", "success")
        else:
            flash(f"Error setting IP address. Check the log for details.", "danger")

    except Exception as e:
        flash(f"Error: {e}", "danger")

    return redirect(url_for('index'))

@app.route('/status')
def status():
    """Show valve status."""
    global valves
    event_object = get_event_loop()
    ids_list = list(event_object.unique_ids)
    i = 1
    for index , items in enumerate(ids_list):
        valves[index+i]['twig_id'] = items
        valves[index+i]["valve_number"] = 1
        valves[index+i+1]['twig_id'] = items
        valves[index+i+1]["valve_number"] = 2
        i+=1
    return render_template('status.html', valves=valves)

def start_flask():
    """Run Flask app in a separate thread."""
    app.run(host='0.0.0.0', port=5000, debug=False)

# some debugging
_debug =  True
_log = ModuleLogger(globals())





@register_object_type
class WritableAnalogValueObject(AnalogValueObject):
    properties = [WritableProperty("presentValue", Real)]
    def WriteProperty(self, property_name, value):
        """Override the WriteProperty method to add COV notification."""
        if property_name == 'presentValue':
            current_value = getattr(self, property_name)
            if current_value != value:
                # Change the value
                super().WriteProperty(property_name, value)
                # Trigger COV notifications
                self.send_cov_notifications()


#
#   SubscribeCOVApplication
#


@bacpypes_debugging
class SubscribeCOVApplication(BIPSimpleApplication, ChangeOfValueServices):
    pass       

#
#   COVConsoleCmd
#


@bacpypes_debugging
class COVConsoleCmd(ConsoleCmd):
    def do_status(self, args):
        print("do status")

        """status"""
        args = args.split()
        if _debug:
            COVConsoleCmd._debug("do_status %r", args)
        global test_application

        # dump from the COV detections dict
        for obj_ref, cov_detection in test_application.cov_detections.items():
            print("{} {}".format(obj_ref.objectIdentifier, obj_ref))

            for cov_subscription in cov_detection.cov_subscriptions:
                print(
                    "    {} proc_id={} confirmed={} lifetime={}".format(
                        cov_subscription.client_addr,
                        cov_subscription.proc_id,
                        cov_subscription.confirmed,
                        cov_subscription.lifetime,
                    )
                )

    def do_trigger(self, args):
        """trigger object_name"""
        print("do trigger")

        args = args.split()
        if _debug:
            COVConsoleCmd._debug("do_trigger %r", args)
        global test_application

        if not args:
            print("object name required")
            return

        obj = test_application.get_object_name(args[0])
        if not obj:
            print("no such object")
            return

        # get the detection algorithm object
        cov_detection = test_application.cov_detections.get(obj, None)
        if (not cov_detection) or (len(cov_detection.cov_subscriptions) == 0):
            print("no subscriptions for that object")
            return

        # tell it to send out notifications
        cov_detection.send_cov_notifications()

    def do_set(self, args):
        print("do set")
        """set object_name [ . ] property_name [ = ] value"""
        args = args.split()
        if _debug:
            COVConsoleCmd._debug("do_set %r", args)
        global test_application

        try:
            object_name = args.pop(0)
            if "." in object_name:
                object_name, property_name = object_name.split(".")
            else:
                property_name = args.pop(0)
            if _debug:
                COVConsoleCmd._debug("    - object_name: %r", object_name)
            if _debug:
                COVConsoleCmd._debug("    - property_name: %r", property_name)

            obj = test_application.get_object_name(object_name)
            if _debug:
                COVConsoleCmd._debug("    - obj: %r", obj)
            if not obj:
                raise RuntimeError("object not found: %r" % (object_name,))

            datatype = obj.get_datatype(property_name)
            if _debug:
                COVConsoleCmd._debug("    - datatype: %r", datatype)
            if not datatype:
                raise RuntimeError("not a property: %r" % (property_name,))

            # toss the equals
            if args[0] == "=":
                args.pop(0)

            # evaluate the value
            value = eval(args.pop(0))
            if _debug:
                COVConsoleCmd._debug("    - raw value: %r", value)

            # see if it can be built
            obj_value = datatype(value)
            if _debug:
                COVConsoleCmd._debug("    - obj_value: %r", obj_value)

            # normalize
            value = obj_value.value
            if _debug:
                COVConsoleCmd._debug("    - normalized value: %r", value)

            # change the value
            setattr(obj, property_name, value)

        except IndexError:
            print(COVConsoleCmd.do_set.__doc__)
        except Exception as err:
            print("exception: %s" % (err,))

    def do_write(self, args):
        """write object_name [ . ] property [ = ] value"""
        args = args.split()
        if _debug:
            COVConsoleCmd._debug("do_set %r", args)
        global test_application

        try:
            object_name = args.pop(0)
            if "." in object_name:
                object_name, property_name = object_name.split(".")
            else:
                property_name = args.pop(0)
            if _debug:
                COVConsoleCmd._debug("    - object_name: %r", object_name)
            if _debug:
                COVConsoleCmd._debug("    - property_name: %r", property_name)

            obj = test_application.get_object_name(object_name)
            if _debug:
                COVConsoleCmd._debug("    - obj: %r", obj)
            if not obj:
                raise RuntimeError("object not found: %r" % (object_name,))

            datatype = obj.get_datatype(property_name)
            if _debug:
                COVConsoleCmd._debug("    - datatype: %r", datatype)
            if not datatype:
                raise RuntimeError("not a property: %r" % (property_name,))

            # toss the equals
            if args[0] == "=":
                args.pop(0)

            # evaluate the value
            value = eval(args.pop(0))
            if _debug:
                COVConsoleCmd._debug("    - raw value: %r", value)

            # see if it can be built
            obj_value = datatype(value)
            if _debug:
                COVConsoleCmd._debug("    - obj_value: %r", obj_value)

            # normalize
            value = obj_value.value
            if _debug:
                COVConsoleCmd._debug("    - normalized value: %r", value)

            # pass it along
            obj.WriteProperty(property_name, value)

        except IndexError:
            print(COVConsoleCmd.do_write.__doc__)
        except Exception as err:
            print("exception: %s" % (err,))


@bacpypes_debugging
class TestAnalogValueTask(RecurringTask):

    """
    An instance of this class is created when '--avtask <interval>' is
    specified as a command line argument.  Every <interval> seconds it
    changes the value of the test_av present value.
    """

    def __init__(self, interval):
        if _debug:
            TestAnalogValueTask._debug("__init__ %r", interval)
        RecurringTask.__init__(self, interval * 1000)

        # make a list of test values
        self.test_values = list(float(i * 10) for i in range(10))

    def process_task(self):
        if _debug:
            TestAnalogValueTask._debug("process_task")
        global test_av

        # pop the next value
        next_value = self.test_values.pop(0)
        self.test_values.append(next_value)
        if _debug:
            TestAnalogValueTask._debug("    - next_value: %r", next_value)

        # change the point
        test_av.presentValue = next_value


@bacpypes_debugging
class TestAnalogValueThread(Thread):

    """
    An instance of this class is created when '--avthread <interval>' is
    specified as a command line argument.  Every <interval> seconds it
    changes the value of the test_av present value.
    """

    def __init__(self, interval):
        if _debug:
            TestAnalogValueThread._debug("__init__ %r", interval)
        Thread.__init__(self)

        # runs as a daemon
        self.daemon = True

        # save the interval
        self.interval = interval

        # make a list of test values
        self.test_values = list(100.0 + float(i * 10) for i in range(10))

    def run(self):
        if _debug:
            TestAnalogValueThread._debug("run")
        global test_av

        while True:
            # pop the next value
            next_value = self.test_values.pop(0)
            self.test_values.append(next_value)
            if _debug:
                TestAnalogValueThread._debug("    - next_value: %r", next_value)

            # change the point
            test_av.presentValue = next_value

            # sleep
            time.sleep(self.interval)

class BinaryPV(Enumerated):
    vendor_range = (0, 1)  # BACnet defines 0 as inactive, 1 as active

@register_object_type
class WritableBinaryValueObject(BinaryValueObject):
    properties = [WritableProperty("presentValue", BinaryPV)]

    def WriteProperty(self, property_name, value, index=None, key=None):
        """Override the WriteProperty method to add custom process."""
        if property_name == 'presentValue':
            current_value = getattr(self, property_name)
            if current_value != value:
                # Change the value
                super().WriteProperty(property_name, value, index, key)

                # Trigger your custom process
                self.on_value_change(value)

    def on_value_change(self, value):
        global valves,object_to_ids_mapping
        """Custom process when value changes."""
        try:
            # Add your custom processing logic here
            print(f"Binary value changed to: {value}")
            print(f"Object Name: {self.objectName}")
            event_object = get_event_loop()
            ids_list = list(event_object.unique_ids)
            i = 1
            for index , items in enumerate(ids_list):
                valves[index+i]['twig_id'] = items
                valves[index+i]["valve_number"] = 1
                valves[index+i+1]['twig_id'] = items
                valves[index+i+1]["valve_number"] = 2
                i+=1
            print(f"DEBUG : {valves}")
            print(f"DEBUG : {object_to_ids_mapping}")
            if len(ids_list) == 0 :
                print("Set is empty")
            elif (int(self.objectName) - 50) > len(ids_list):
                print(f'The number of valves is {len(ids_list)} max object number is {len(ids_list) + 50} given object is {self.objectName}')  
            else:        
                if (valves[object_to_ids_mapping[self.objectName]]["valve_number"]) ==1:
                    valves[int(self.objectName) - 50]['status'] =  "Open" if value==1 else "Closed"
                    control_valve(valves[object_to_ids_mapping[self.objectName]]["twig_id"].to_bytes(4,byteorder='little') , 0x01 if value==1 else 0x02)
                elif(valves[object_to_ids_mapping[self.objectName]]["valve_number"]) ==2:
                    control_valve(valves[object_to_ids_mapping[self.objectName]]["twig_id"].to_bytes(4,byteorder='little') , 0x04 if value==1 else 0x08)
                    valves[object_to_ids_mapping[self.objectName]]['status'] =  "Open" if value==1 else "Closed"


                # if (valves[int(self.objectName) - 50]["valve_number"] ) ==1:
                #     valves[int(self.objectName) - 50]['status'] =  "Open" if value==1 else "Closed"
                #     control_valve(valves[int(self.objectName)-50]["twig_id"].to_bytes(4,byteorder='little') , 0x01 if value==1 else 0x02)
                # elif(valves[int(self.objectName) - 50]["valve_number"] ) ==2:
                #     control_valve(valves[int(self.objectName)-50]["twig_id"].to_bytes(4,byteorder='little') , 0x04 if value==1 else 0x08)
                #     valves[int(self.objectName) - 50]['status'] =  "Open" if value==1 else "Closed"


        except Exception as e:
            print(e)
@bacpypes_debugging
class TestBinaryValueTask(RecurringTask):

    """
    An instance of this class is created when '--bvtask <interval>' is
    specified as a command line argument.  Every <interval> seconds it
    changes the value of the test_bv present value.
    """

    def __init__(self, interval):
        if _debug:
            TestBinaryValueTask._debug("__init__ %r", interval)
        RecurringTask.__init__(self, interval * 1000)

        # save the interval
        self.interval = interval

        # make a list of test values
        self.test_values = [True, False]

    def process_task(self):
        if _debug:
            TestBinaryValueTask._debug("process_task")
        global test_bv

        # pop the next value
        next_value = self.test_values.pop(0)
        self.test_values.append(next_value)
        if _debug:
            TestBinaryValueTask._debug("    - next_value: %r", next_value)

        # change the point
        test_bv.presentValue = next_value


@bacpypes_debugging
class TestBinaryValueThread(RecurringTask, Thread):

    """
    An instance of this class is created when '--bvthread <interval>' is
    specified as a command line argument.  Every <interval> seconds it
    changes the value of the test_bv present value.
    """

    def __init__(self, interval):
        if _debug:
            TestBinaryValueThread._debug("__init__ %r", interval)
        Thread.__init__(self)

        # runs as a daemon
        self.daemon = True

        # save the interval
        self.interval = interval

        # make a list of test values
        self.test_values = [True, False]

    def run(self):
        global test_bv
        while not stop_event.is_set():
            next_value = self.test_values.pop(0)
            self.test_values.append(next_value)
            test_bv.presentValue = next_value
            time.sleep(self.interval)


def main():
    # Register signal handlers
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except:
        pass
    return_status = setup()  # setup twig protocol

    global test_av, test_bv, test_application, num_valves, object_to_ids_mapping
    # load the configuration
    load_config()
    print(f'Number of valves is {num_valves}')

    # make a parser
    parser = ConfigArgumentParser(description=__doc__)
    parser.add_argument(
        "--console", action="store_true", default=False, help="create a console",
    )

    # analog value task and thread
    parser.add_argument(
        "--avtask", type=float, help="analog value recurring task",
    )
    parser.add_argument(
        "--avthread", type=float, help="analog value thread",
    )

    # analog value task and thread
    parser.add_argument(
        "--bvtask", type=float, help="binary value recurring task",
    )
    parser.add_argument(
        "--bvthread", type=float, help="binary value thread",
    )

    # provide a different spin value
    parser.add_argument(
        "--spin", type=float, help="spin time", default=1.0,
    )
    if not hasattr(parser, 'ini') or not parser.parse_args().ini:
        parser.set_defaults(ini='./bacnet.ini')
    # parse the command line arguments
    args = parser.parse_args()

    if _debug:
        _log.debug("initialization")
    if _debug:
        _log.debug("    - args: %r", args)

    # Check for stop signal
    if stop_event.is_set():
        print("Stop signal received. Exiting main function.")
        return

    # make a device object
    this_device = LocalDeviceObject(ini=args.ini)
    if _debug:
        _log.debug("    - this_device: %r", this_device)

    # Check for stop signal
    if stop_event.is_set():
        print("Stop signal received. Exiting main function.")
        return

    # make a sample application
    print(args.ini.address)

    test_application = SubscribeCOVApplication(this_device, args.ini.address)

    # Check for stop signal
    if stop_event.is_set():
        print("Stop signal received. Exiting main function.")
        return

    # make a binary value object
    for i in range(1, num_valves + 1):
        test_bv = WritableBinaryValueObject(
            objectIdentifier=("binaryValue", i),
            objectName=f"{50 + i}",
            presentValue=BinaryPV(0),
            statusFlags=[0, 0, 0, 0],
        )
        object_to_ids_mapping[f"{50 + i}"] = 0

        # add it to the device
        test_application.add_object(test_bv)

        # Check for stop signal in the loop
        if stop_event.is_set():
            print("Stop signal received. Exiting main function.")
            return

    _log.debug("    - test_bv: %r", test_bv)

    # make a console
    if args.console:
        test_console = COVConsoleCmd()
        _log.debug("    - test_console: %r", test_console)

        # enable sleeping will help with threads
        enable_sleeping()

    # analog value task
    if args.avtask:
        test_av_task = TestAnalogValueTask(args.avtask)
        test_av_task.install_task()

    # analog value thread
    if args.avthread:
        test_av_thread = TestAnalogValueThread(args.avthread)
        deferred(test_av_thread.start)

    # binary value task
    if args.bvtask:
        test_bv_task = TestBinaryValueTask(args.bvtask)
        test_bv_task.install_task()

    # binary value thread
    if args.bvthread:
        test_bv_thread = TestBinaryValueThread(args.bvthread)
        deferred(test_bv_thread.start)

    _log.debug("running")

    # Broadcast Who-Is request
    who_is = WhoIsRequest()
    who_is.pduDestination = Address("192.168.30.255")  # Broadcast to all devices on the network
    i_am = IAmRequest()
    i_am.pduDestination = Address("192.168.30.255")
    i_am.iAmDeviceIdentifier = this_device.objectIdentifier
    i_am.maxAPDULengthAccepted = this_device.maxApduLengthAccepted
    i_am.segmentationSupported = this_device.segmentationSupported
    i_am.vendorID = this_device.vendorIdentifier
    test_application.request(i_am)

    # Start Flask in a separate thread
    flask_thread = Thread(target=start_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Check for stop signal in the main loop
    while not stop_event.is_set():
        run(args.spin)

    # Cleanup when the stop signal is received
    print("Cleaning up resources...")
    test_application = None
    _log.debug("fini")



def control_valve(oid, action):
    """
    Sends commands to control two valves on a twig using a single integer.

    :param oid: Object Identifier (as bytes) of the twig, e.g., b'\xE0\xE1\x10\x00'
    :param action: Integer bitmask (0-15) representing the state of two valves:
                   - Bit 0: Valve 1 ON
                   - Bit 1: Valve 1 OFF
                   - Bit 2: Valve 2 ON
                   - Bit 3: Valve 2 OFF
                   Example: 13 (0b1101) => Valve 1 ON, Valve 2 ON, Valve 2 OFF
    """
    commandLoop = get_command_loop()
    
    # Validate action
    if not (0 <= action <= 0x0F):  # Ensure action is within 4 bits (0–15)
        raise ValueError("Invalid action. Must be an integer between 0 and 15.")

    # Step 1: Send valvesBegin command (0x02)
    commandLoop.queueNamedCommand(CommandCode.ValvesBegin)
    print("Sent: valvesBegin (0x02)")
    print(f'Type pf oid is {type(oid)}')


    # Step 3: Send valvesPut command (0x51)
    body = oid + bytes([action])
    commandLoop.queueNamedCommand(CommandCode.ValvesPut, body)
    print(f"Sent: valvesPut (0x51) for OID {HEX(oid)}, action: {action}")

    # Step 4: Send valvesCommit command (0x04)
    commandLoop.queueNamedCommand(CommandCode.ValvesCommit)
    print("Sent: valvesCommit (0x04)")

if __name__ == "__main__":
    main()
