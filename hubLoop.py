#!/usr/bin/env python3

import queue
import struct
import sys
import threading
from datetime import datetime, timezone
from time import sleep
from helper import *
import serial


from lib import packet_codes
from lib.central_control_types import CommandCode, EventCode
from lib.position_codes import PositionCode
from lib.utils import HEX
from lib.twigIDs import TwigID

from typing import Dict, Callable

eventLoop = None

commandLoop = None
# Every command has a 16 bit fletcher appended. The seeding with 0x600D is important
def fletcher16(bits):
	sum2 = 0x60
	sum1 = 0x0D
	for byte in bits:
		sum1 += byte
		sum1 %= 255
		sum2 += sum1
		sum2 %= 255
	return bytes((sum1, sum2))


class HubCommandLoop(object):
	# Responsible for queing and dispatching commands to the hub
	# The hub has no buffering ability, so it is important that commands
	# are buffered here in the "commands" variable and reeled out only after events related to tehir send
	# In initial set of 5 commands are issued at startup to harvest information from the hub
	def __init__(self, port: serial.Serial):
		self.port = port
		self.activeCommand = None
		self.retryCount = 0
		self.commands = queue.SimpleQueue()
		self.events = queue.SimpleQueue()
		self.validators: Dict[CommandCode, Callable[[bytes], bool]] = {
			# assume VitalsGet a 0x000 all vitals variant
			CommandCode.VitalsGet: lambda bits: self.validateEventCode(bits, EventCode.CommandSuccess),
			CommandCode.VersionsGet: lambda bits: self.validateEventCode(bits, EventCode.Versions),
			CommandCode.Channel: lambda bits: self.validateEventCode(bits, EventCode.Channel),
			CommandCode.NetIDGet: lambda bits: self.validateEventCode(bits, EventCode.NetID),
			CommandCode.ValvesBegin: lambda bits: self.validateEventCode(bits, EventCode.CommandSuccess),
			CommandCode.ValvesCommit: lambda bits: self.validateEventCode(bits, EventCode.CommandSuccess),
			CommandCode.PairingPatternGet: lambda bits: self.validateEventCode(bits, EventCode.PairingPattern),
			CommandCode.PairingPatternGenerate: lambda bits: self.validateEventCode(bits, EventCode.PairingPattern),
			CommandCode.Forget: lambda bits: self.validateEventCode(bits, EventCode.CommandSuccess),
			CommandCode.ValvesPut: self.validateValvesSet,
		}

	def noteEvent(self, bits):
		if EventCode(bits[0]).isSolicited:
			self.events.put(bits)

	def queueNamedCommand(self, commandCode, body=None):
		bits = bytes([commandCode])
		if body:
			bits += body
		self.queueCommandBits(bits)

	def queueCommandBits(self, bits):
		raw = bits + fletcher16(bits)
		self.commands.put(raw)

	def putCommandOnWire(self):
		global eventLoop
		packet = packet_codes.escapePacketCodes(self.activeCommand)
		toSend = bytes(packet).join(packet_codes.PacketCode.byteEnds())
		self.port.write(toSend)
		print(f'send[{HEX(toSend)}]')
		eventLoop.append_to_list(f'send[{HEX(toSend)}]')


	def validateValvesSet(self, eventBits) -> bool:
		if eventBits[0] != EventCode.Valves:
			return False
		active_oid, _ = struct.unpack_from("<IB", self.activeCommand, 1)
		event_oid, _ = struct.unpack_from("<IB", eventBits, 1)
		return active_oid == event_oid

	def validateEventCode(self, eventBits, desiredCode):
		return eventBits[0] == desiredCode

	def validateResponse(self, eventBits):
		validator = self.validators.get(self.activeCommand[0], None)
		if validator:
			if not validator(eventBits):
				print(f"UNEXPECTED {HEX(self.activeCommand)} {HEX(eventBits)}")
				self.waitForResponse()
		else:
			print(f"NO VALIDATOR {HEX(self.activeCommand)}")

	def waitForResponse(self):
		global eventLoop
		try:
			responseBits = self.events.get(timeout=0.6)
		except queue.Empty:
			print(f"ERROR no response for {HEX(self.activeCommand)}")
			eventLoop.append_to_list(f"ERROR no response for {HEX(self.activeCommand)}")

			return
		if EventCode(responseBits[0]).isTransmissionError:
			if self.retryCount < 3:
				self.retryCount += 1
				print(f"RETRY {self.retryCount} {HEX(self.activeCommand)}")
				sleep(0.01)
				self.drainEvents()
				self.putCommandOnWire()
				self.waitForResponse()
				return
			else:
				print(f"RETRY MAX {HEX(self.activeCommand)}")
				return
		self.validateResponse(responseBits)

	def drainEvents(self):
		while self.events.qsize():
			self.events.get_nowait()

	def step(self):
		self.activeCommand = self.commands.get()
		self.drainEvents()
		self.putCommandOnWire()
		self.retryCount = 0
		self.waitForResponse()

	def resetCommandStream(self):
		# emit a stream of time spaced empty packets to flush/reset the command stream
		# this may cause some debug wth output on the hub
		for _ in range(3):
			self.port.write(packet_codes.packetize(b""))
			sleep(0.05)

	def queueStartupCommands(self):
		self.queueNamedCommand(CommandCode.NetIDGet)
		self.queueNamedCommand(CommandCode.Channel, bytes([0]))
		self.queueNamedCommand(CommandCode.VersionsGet)
		self.queueNamedCommand(CommandCode.PairingPatternGet)
		self.queueNamedCommand(CommandCode.VitalsGet, struct.pack("<I", 0))

	def loop(self):
		self.resetCommandStream()
		self.queueStartupCommands()
		while True:
			self.step()



class HubEventLoop(object):
	# The event loop handles the reading of the serial port and decoding of events
	# The eventXXX methods can be used as templates for callbacks to ingest network related information into the host system
	# It also relays events to the CommandLoop, so that the commandLoop can validate the reception of its commands and queue any retries accordingly
	def __init__(self, port, commandLoop: HubCommandLoop):
		super().__init__()
		self.port = port
		self.commandLoop = commandLoop
		self.unique_ids = set()
		self.communication_log=[]
		self.isLoRa = False
		self.dispatchTable = {
			EventCode.CycleStartImminent: self.eventCycleStartImminent,
			EventCode.CommandErrorNotFound: self.eventCommandErrorNotFound,
			EventCode.CommandErrorIllegal: self.eventCommandErrorIllegal,
			EventCode.CommandSuccess: self.eventCommandSuccess,
			EventCode.CommandErrorSize: self.eventCommandErrorSize,
			EventCode.PairingPattern: self.eventPairingPattern,
			EventCode.Channel: self.eventChannel,
			EventCode.NetID: self.eventNetID,
			EventCode.CommandErrorChecksum: self.eventCommandErrorChecksum,
			EventCode.Valves: self.eventValves,
			EventCode.Versions: self.eventVersions,
			EventCode.SubnetInfo: self.eventSubnet,
			EventCode.Vitals: self.eventVitals,
			EventCode.AllVitalsReported: self.eventAllVitalsReported,
		}

	def dispatch(self, packet):
		if len(packet) < 3:
			print("!short_event", HEX(packet))
			return
		event, preChecksum = packet[:-2], packet[-2:]
		postChecksum = fletcher16(event)
		if postChecksum != preChecksum:
			print("!checksum_pre", HEX(preChecksum), "!= post", HEX(postChecksum), HEX(packet))
			return
		eventCode = event[0]
		method = self.dispatchTable.get(eventCode, None)
		if method is not None:
			method(event[1:])
		else:
			print("!unknown_event", HEX(event))
		self.commandLoop.noteEvent(event)

	def eventVitals(self, eventBody): 
		oid, _pow, rssi, valves, extra = struct.unpack("<IHBHH", eventBody)
		print(f'<< rtu oid={oid}, rssi={rssi}, valves={valves:04X}, extra={extra:04X}')
		self.unique_ids.add(oid)

	def eventSubnet(self, eventBody):
		oid, subnet = struct.unpack("<II", eventBody)
		print(f'<< subnet oid={oid}, subnet={subnet}')
		self.append_to_list(f'<< subnet oid={oid}, subnet={subnet}')

	def eventCycleStartImminent(self, _):  # this should only ever happen on a 174 network
		print(f'<< cycleStart')

	def eventNetID(self, eventBody):
		(netID,) = struct.unpack("<I", eventBody)
		twigID: TwigID = TwigID.int(netID)
		self.isLoRa = twigID.isLoRa
		print(f'<< netid={netID}')

	def eventVersions(self, eventBody):
		protocol, network, git = struct.unpack("<BB8s", eventBody)
		git = git.strip(b"\x00").decode("ascii")
		print(f'<< versions git={git}, protocol={protocol}, network={network}')
		self.append_to_list(f'<< versions git={git}, protocol={protocol}, network={network}')

	def eventChannel(self, eventBody):
		channel, low, high = struct.unpack("<BBB", eventBody)
		print(f'<< channel={channel}, min={low}, max={high}')
		self.append_to_list(f'<< channel={channel}, min={low}, max={high}')

	def eventPairingPattern(self, eventBody):
		(pattern,) = struct.unpack("<H", eventBody)
		print(f'<< pairingPattern={pattern:09b}')

	def eventAllVitalsReported(self, _):
		print(f'<< allVitalsReported')

	def eventCommandSuccess(self, _):
		pass

	def eventValves(self, _):
		pass

	def eventCommandErrorChecksum(self, eventBody):
		print(f"ERROR checksum {HEX(eventBody)}")

	def eventCommandErrorIllegal(self, eventBody):
		print(f"ERROR illegal {HEX(eventBody)}")

	def eventCommandErrorSize(self, eventBody):
		print(f"ERROR size {HEX(eventBody)}")

	def eventCommandErrorNotFound(self, eventBody):
		print(f"ERROR not found {HEX(eventBody)}")

	def loop(self):
		global eventLoop
		isEscaped = False
		packet = bytearray()
		while True:
			bits = self.port.read(1) # this will block
			bits += self.port.read(self.port.in_waiting) # this will not, but will grab any other buffered bytes
			print(f'received[{HEX(bits)}]')
			self.append_to_list(f'received[{HEX(bits)}]')

			# simple loop for unescaping the byte stream and deframing the packets
			for byte in bits:
				if byte == packet_codes.PacketCode.Start:
					packet = bytearray()
				elif byte == packet_codes.PacketCode.Stop:
					self.dispatch(packet)
				elif byte == packet_codes.PacketCode.Escape:
					isEscaped = True
				else:
					packet.append(byte ^ 0xFF if isEscaped else byte)
					isEscaped = False
			if isEscaped == True:
				print(f'escaped[{HEX(bits)}]')
	def append_to_list(self,item):
		# Add the item with a timestamp
		self.communication_log.append({"timestamp": datetime.now().isoformat(), "value": item})
		# Keep the list size to a maximum of 10
		print(self.communication_log)
		if len(self.communication_log) > 20:
			self.communication_log.pop(0)  # Remove the oldest item

def get_event_loop():
    global eventLoop
    if eventLoop is None:
        raise RuntimeError("eventLoop is not initialized. Did you call setup()?")  
    return eventLoop
def get_command_loop():
    global commandLoop
    if commandLoop is None:
        raise RuntimeError("commandLoop is not initialized. Did you call setup()?")  
    return commandLoop

def setup():
	global eventLoop
	global commandLoop
	#portPath = sys.argv[1] # should be something like "/dev/ttyS1"
	portPath = '/dev/ttyUSB0'
	try:
		port = serial.Serial(portPath, stopbits=serial.STOPBITS_ONE, baudrate=115200, timeout=None)
	except Exception as e:
		print(e)
		return CONNECTION_ERROR

	# create a loop object to handle each side of serial communcations (command for sending, event for consuming responses and other async data)
	commandLoop = HubCommandLoop(port)
	eventLoop = HubEventLoop(port, commandLoop)

	# launch threads to run each loop
	eventThread = threading.Thread(target=eventLoop.loop)
	eventThread.start()
	commandThread = threading.Thread(target=commandLoop.loop)
	commandThread.start()
	return OK
	# # now wait for user input to send to the hub
	# while True:
	# 	commandText = input('Command (hex):')
	# 	commandWords = commandText.split()
	# 	commandBytes = None
	# 	try:
	# 		commandBytes = bytes(int(nibble, 16) for nibble in commandWords)
	# 	except ValueError:
	# 		continue
	# 	if commandBytes:
	# 		print(f'send[{HEX(commandBytes)}]')
	# 		commandLoop.queueCommandBits(commandBytes)


# if __name__ == "__main__":
# 	main()
