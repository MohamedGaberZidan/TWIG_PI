from __future__ import annotations
import enum


@enum.unique
class PositionCode(enum.IntEnum):
	Unknown = 0
	Off = 1
	On = 2
	Illegal = 3

	@classmethod
	def int(cls, value, default=Illegal):
		if isinstance(value, int) and 0 <= value <= 2:
			return cls(value)
		else:
			return default

	@classmethod
	def fromTSONFile(cls, path) -> PositionCode:
		from lib import tson

		try:
			return PositionCode.int(tson.decodePath(path), default=cls.Unknown)
		except (FileNotFoundError, tson.TSONError) as e:
			print(e)
			return cls.Unknown

	@property
	def shortName(self) -> str:
		return "?" if self == PositionCode.Unknown else self.name.lower()
