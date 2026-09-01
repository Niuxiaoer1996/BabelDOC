import copy
import dataclasses
import json
import logging
from pathlib import Path

from xsdata.formats.dataclass.context import XmlContext
from xsdata.formats.dataclass.parsers import XmlParser
from xsdata.formats.dataclass.serializers import XmlSerializer
from xsdata.formats.dataclass.serializers.config import SerializerConfig

from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
    LazyPassthroughInstruction,
)

logger = logging.getLogger(__name__)


def _json_default(value):
    """Python json 序列化兜底：处理 IL 中的 dataclass 与 LazyPassthroughInstruction。

    供 write_json 的流式 json.dump 使用（标准库 json 不原生支持 dataclass）。
    注意 IL 为 @dataclass(slots=True)，无 __dict__，需用 dataclasses.fields 展开。
    """
    if isinstance(value, LazyPassthroughInstruction):
        return value.materialize()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class XMLConverter:
    def __init__(self):
        self.parser = XmlParser()
        config = SerializerConfig(indent="  ")
        context = XmlContext()
        self.serializer = XmlSerializer(context=context, config=config)

    def write_xml(self, document: il_version_1.Document, path: str):
        with Path(path).open("w", encoding="utf-8") as f:
            f.write(self.to_xml(document))

    def read_xml(self, path: str) -> il_version_1.Document:
        with Path(path).open(encoding="utf-8") as f:
            return self.from_xml(f.read())

    def to_xml(self, document: il_version_1.Document) -> str:
        return self.serializer.render(document)

    def from_xml(self, xml: str) -> il_version_1.Document:
        return self.parser.from_string(
            xml,
            il_version_1.Document,
        )

    def deepcopy(self, document: il_version_1.Document) -> il_version_1.Document:
        return copy.deepcopy(document)
        # return self.from_xml(self.to_xml(document))

    def to_json(self, document: il_version_1.Document) -> str:
        return json.dumps(
            document,
            default=_json_default,
            ensure_ascii=False,
            indent=2,
        )

    def write_json(self, document: il_version_1.Document, path: str):
        """把 IL 文档流式写入 JSON 文件（内存安全）。

        原实现用 orjson.dumps(...).decode() 一次性构建完整 JSON 字符串再写入。
        对密集大文档（如 JESD238B.01 的 IL 可达数百 MB，orjson 还叠加 indent 膨胀
        数倍），在已占用 ~1.5GB 对象树的内存之上再构建整串 JSON 会触发 MemoryError。
        这里改用标准库 json.dump 逐块写入文件，内存占用仅与文档对象树相当，
        不再额外叠加整串 JSON。失败时记录警告并跳过，避免 debug 转储失败中断翻译。
        """
        try:
            with Path(path).open("w", encoding="utf-8") as f:
                json.dump(
                    document,
                    f,
                    default=_json_default,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as e:
            logger.warning(f"Failed to write debug JSON {path}: {e}")
