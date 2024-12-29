def HEX(bits: bytes) -> str:
	return ":".join("{:02X}".format(byte) for byte in bits)
