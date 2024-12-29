class TwigID(object):
	def __init__(self, value: int):
		self.value: int = value

	@classmethod
	def int(cls, value):
		if value < 10:
			return TwigIDLocalValve(value)
		elif value < 0x0100_0000:
			return TwigIDLoRa(value)
		else:
			return TwigIDSiFlex(value)

	@property
	def valveCount(self) -> int:
		raise NotImplementedError

	@property
	def gaugeCount(self) -> int:
		return 0  # LoRa type 9 overrides this

	@property
	def hasValves(self) -> bool:
		return self.valveCount > 0

	@property
	def rtuString(self) -> str:
		# noinspection PyTypeChecker
		return NotImplementedError

	@property
	def valveString(self) -> str:
		# noinspection PyTypeChecker
		return NotImplementedError

	@property
	def debugString(self) -> str:
		rtuString: str = self.rtuString
		return rtuString if self.valveCount <= 1 else rtuString + f"#{self.valveIndex}"

	@property
	def valveIndex(self) -> int:
		return NotImplementedError

	@property
	def isLoRa(self) -> bool:
		return False

	@property
	def isSiFlex(self) -> bool:
		return False

	@property
	def isLocal(self) -> bool:
		return False

	@property
	def nameToken(self) -> str:
		return "V" if self.valveIndex else "R"

	@property
	def hasVerification(self) -> bool:
		return False

	def valves(self):
		for offset in range(self.valveCount):
			yield type(self)(self.value + 1 + offset)


class TwigIDSiFlex(TwigID):
	@property
	def valveCount(self) -> int:
		typeCode: int = (self.value & 0x0F000000) >> 24
		try:
			return (1, 2, 4, 4)[typeCode - 0xA]  # A, B, C, D
		except IndexError:
			return 0

	@property
	def rtuString(self) -> str:
		return "{:X}".format(self.value)[:-1]

	@property
	def valveString(self) -> str:
		return "{:X}".format(self.value)

	@property
	def valveIndex(self) -> int:
		return self.value & 0xF

	@property
	def isSiFlex(self) -> bool:
		return True


class TwigIDLoRa(TwigID):
	@property
	def typeCode(self) -> int:
		return self.value // 1_000_000

	@property
	def valveCount(self) -> int:
		try:
			return (0, 1, 2, 0, 0, 1, 2, 0, 0, 0)[self.typeCode]
		except IndexError:
			return 0

	@property
	def gaugeCount(self) -> int:
		return 2 if self.typeCode == 9 else 0

	@property
	def hasVerification(self) -> bool:
		return self.typeCode in (5, 6)

	@property
	def rtuString(self) -> str:
		return "{}-{:05d}".format(self.typeCode, (self.value % 1_000_000) // 10)

	@property
	def valveString(self) -> str:
		return f"{self.rtuString}-{self.valveIndex}"

	@property
	def valveIndex(self) -> int:
		return self.value % 10

	@property
	def isLoRa(self) -> bool:
		return True


class TwigIDLocalValve(TwigID):
	@property
	def valveCount(self) -> int:
		return 2

	@property
	def rtuString(self) -> str:
		return "MC"

	@property
	def valveString(self) -> str:
		return f"MC.{self.value}"

	@property
	def valveIndex(self) -> int:
		return self.value

	@property
	def isLocal(self) -> bool:
		return True

	@property
	def nameToken(self) -> str:
		return "L"
