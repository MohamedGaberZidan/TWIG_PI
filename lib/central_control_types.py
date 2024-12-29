import enum


@enum.unique
class CommandCode(enum.IntEnum):
	# 0 byte body
	VersionsGet = 0x01
	ValvesBegin = 0x02
	NetIDGet = 0x03
	ValvesCommit = 0x04
	#   IsNetLockedGet = 0x05 #RETIRED
	PairingPatternGenerate = 0x06
	PairingPatternGet = 0x07
	# 1 byte body
	Channel = 0x11  # 00 to not set
	#   IsNetLockedPut = 0x12 # 0/1 (false/true) # RETIRED
	# 4 byte body
	Test = 0x41  # 4 test bytes, will be inverted
	VitalsGet = 0x42  # node id (or 0x00000000 to get all)
	Forget = 0x43  # node id
	# 5 byte body
	ValvesPut = 0x51  # node id, positions (packed)


@enum.unique
class EventCode(enum.IntEnum):
	# 0 byte body
	CycleStartImminent = 0x01  # ASYNCHRONOUS
	AllVitalsReported = 0x02  # ASYNCHRONOUS
	# 1 byte body
	CommandErrorNotFound = 0x12  # command, solicited
	CommandErrorIllegal = 0x13  # command, solicited
	CommandSuccess = 0x14  # command, solicited
	# 2 byte body
	CommandErrorSize = 0x22  # command, passed, solicited
	PairingPattern = 0x23  # 16 bits(0 - 511) pairing pattern, solicited
	# 3 byte body
	Channel = 0x31  # my current settings channel, min, and max, solicited
	# 4 byte body
	Test = 0x41  # bit invert of whatever data 4 bytes, solicited
	NetID = 0x42  # my central id, solicited
	# 5 byte body
	CommandErrorChecksum = 0x51  # command, pre, post, solicited
	Valves = 0x52  # node id, positions (packed), solicited
	# 8 byte body
	SubnetInfo = 0x81  # idRTU, idSubnet, ASYNCHRONOUS
	# 10 byte body
	Versions = 0xA1  # protocol version, network version, 8 bytes Git version, solicited
	# 15 byte body
	Vitals = 0xB1  # id + NodeStatus, ASYNCHRONOUS

	@property
	def isTransmissionError(self):
		# do not include Illegal, since those don't happen due to serial rx/tx errors, but rather malformed parameters
		return self in (
			EventCode.CommandErrorNotFound,
			EventCode.CommandErrorSize,
			EventCode.CommandErrorChecksum,
		)

	@property
	def isSolicited(self):
		return self not in (EventCode.CycleStartImminent, EventCode.AllVitalsReported, EventCode.Vitals, EventCode.SubnetInfo)
