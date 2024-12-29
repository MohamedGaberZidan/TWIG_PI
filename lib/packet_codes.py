from enum import IntEnum
from enum import unique
from typing import Generator


@unique
class PacketCode(IntEnum):
	Start = 0x83
	Stop = 0x87
	Escape = 0x88

	# noinspection PyRedundantParentheses
	@classmethod
	def byteEnds(cls):
		return (bytes([cls.Start]), bytes([cls.Stop]))


# noinspection PyTypeChecker
EscapedCodes = bytes(PacketCode)  # can't test for bytes in an enum, only enum members


def escapePacketCodes(bits) -> Generator[int, None, None]:
	for byte in bits:
		if byte in EscapedCodes:
			yield PacketCode.Escape
			yield byte ^ 0xFF
		else:
			yield byte


def unescapePacketCodes(bits) -> Generator[int, None, None]:
	isEscaping = False
	for byte in bits:
		if isEscaping:
			isEscaping = False
			yield byte ^ 0xFF
		else:
			if byte == PacketCode.Escape:
				isEscaping = True
			else:
				yield byte


def packetize(bits) -> bytes:
	return bytes(escapePacketCodes(bits)).join(PacketCode.byteEnds())
