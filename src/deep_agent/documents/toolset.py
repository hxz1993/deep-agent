from __future__ import annotations

import asyncio
import hashlib
import io
import time
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed
from pypdf import PdfReader

from deep_agent.agent.attachment_extractors import AttachmentParseError, DocumentPage as ReadingPage
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.diagnostics import emit
from deep_agent.files.reading_budget import ReadingBudget, paginate_items
from deep_agent.files.toolset import load_file_bytes, resolve_file_source
from deep_agent.skills.runtime import ResolvedSkill

from .cache import CachedDocument, DocumentRunCache
from .models import DocumentParsingError, DocumentSource
from .providers import PROVIDERS, parse_document as parse_with_provider
from .schema import DocumentReadResult, DocumentRef, DocumentResource, DocumentSummary, PageFragment
from .settings import DocumentParsingRuntimeConfig

_RESOURCE = TypeAdapter(DocumentResource)


def _validate_range(**values: int | None) -> None:
    for name, value in values.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < (0 if name == "char_offset" else 1)):
            raise ModelRetry(f"{name} 必须是{'非负' if name == 'char_offset' else '正'}整数")
    if values.get("page_start") and values.get("page_end") and values["page_start"] > values["page_end"]:
        raise ModelRetry("page_end 不能小于 page_start")


def _source_pages(data: bytes, mime: str) -> int | None:
    if mime in {"image/png", "image/jpeg"}:
        return 1
    if mime != "application/pdf":
        return None
    try:
        with PdfReader(io.BytesIO(data), strict=True) as reader:
            if reader.is_encrypted:
                raise ModelRetry("加密 PDF 不受支持；请先提供已解密的原件")
            count = len(reader.pages)
        if not count:
            raise ModelRetry("PDF 没有可解析页面")
        return count
    except ModelRetry:
        raise
    except Exception as exc:
        raise ModelRetry("无法读取 PDF 页数；请确认文件完整且未损坏") from exc


def _summary(record: CachedDocument) -> tuple[DocumentSummary, list[str], str]:
    parsed = record.parsed
    numbers = [page.page for page in parsed.pages]
    known = all(number is not None for number in numbers) and len(set(numbers)) == len(numbers)
    missing = []
    coverage = "unknown"
    warnings = list(parsed.warnings)
    status = parsed.status
    if known and parsed.source_pages is not None:
        missing = sorted(set(range(1, parsed.source_pages + 1)) - set(numbers))
        coverage = "partial" if missing else "complete"
        if missing:
            status = "partial"
            warnings.append("供应商返回结果缺少原始页面，当前缓存读完不表示原文已完整解析。")
    elif not known:
        warnings.append("供应商未提供完整可靠的原始页码；可按返回序列续读，不能按原始页范围选择。")
    return DocumentSummary(source_pages=parsed.source_pages, parsed_pages=len(parsed.pages),
                           page_numbers_known=known, missing_pages=missing, coverage=coverage), warnings, status


def read_cached_document(
    record: CachedDocument, *, budget: ReadingBudget, page_start: int | None = None,
    page_end: int | None = None, limit: int | None = None, offset: int = 1, char_offset: int = 0,
) -> DocumentReadResult:
    _validate_range(page_start=page_start, page_end=page_end, limit=limit, offset=offset, char_offset=char_offset)
    summary, warnings, status = _summary(record)
    selected = record.readable.pages
    if page_start is not None or page_end is not None:
        if not summary.page_numbers_known:
            raise ModelRetry("该供应商结果没有可靠原始页码，请省略 page_start/page_end 并按 next_read 续读")
        selected = [page for page in selected
                    if (page_start is None or page.page >= page_start) and (page_end is None or page.page <= page_end)]
    page_limit = limit if limit is not None else max(1, len(selected) - offset + 1)
    candidates = selected[offset - 1:offset - 1 + page_limit]

    def result(page: ReadingPage) -> DocumentReadResult:
        fragments = []
        for index, native in enumerate(candidates[:page.returned_count]):
            start = char_offset if index == 0 else 0
            # The paginator probes a single page prefix before deciding whether
            # it is complete. Measure that exact prefix, including probe envelopes.
            end = start + len(page.content) if page.returned_count == 1 else len(native.content)
            fragments.append(PageFragment(page_number=native.page, format=native.format,
                                          content=native.content[start:end], char_offset=start,
                                          char_end=end, complete=end == len(native.content)))
        next_read = None
        if page.has_more:
            next_read = {"resource": {"type": "document", "document_id": record.document_id},
                         "offset": page.next_offset, "char_offset": page.next_char_offset}
            for name, value in (("page_start", page_start), ("page_end", page_end), ("limit", limit)):
                if value is not None:
                    next_read[name] = value
        read_warnings = list(warnings)
        if page.has_more:
            read_warnings.append("当前仅返回部分正文，尚未读完；需要全文审查或提取时必须按 next_read 继续，不能声称已读完整文件。")
        if len(selected) != len(record.parsed.pages):
            read_warnings.append("本次只读取指定原始页范围；该范围读完不表示已读完整文件。")
        return DocumentReadResult(
            document=DocumentRef(document_id=record.document_id), provider=record.parsed.provider,
            model=record.parsed.model, status=status, summary=summary, pages=fragments, warnings=read_warnings,
            offset=offset, char_offset=char_offset, limit=page_limit, returned_count=page.returned_count,
            has_more=page.has_more, next_read=next_read,
        )

    try:
        page = paginate_items([page.content for page in candidates], total_count=len(selected),
                              offset=offset, limit=page_limit, budget=budget, char_offset=char_offset,
                              envelope=lambda value: result(value).model_dump(mode="json"))
    except AttachmentParseError as exc:
        raise ModelRetry(str(exc)) from exc
    return result(page)


def build_document_toolset(
    config: DocumentParsingRuntimeConfig, resources: dict[str, ResolvedSkill] | None = None, *,
    context_window_tokens: int | None = None, estimator: TokenEstimator | None = None,
) -> FunctionToolset[AgentDeps]:
    budget = ReadingBudget(estimator or TokenEstimator.conservative(), context_window_tokens)
    authorized = dict(resources or {})
    capability = PROVIDERS.get(config.provider)
    formats = ", ".join(capability.describe()["formats"]) if capability else "未配置"
    description = (
        f"专业文档解析，当前供应商 {capability.label if capability else '未配置'}，支持 {formats}。"
        "PDF（包括带文字层的PDF）直接使用本工具，不要先调用read_file试读；"
        "若本轮同时提供read_file，常规文件读取使用它支持的读取能力；需要OCR、版面或复杂表格时使用本工具。"
        "仅支持当前供应商列出的格式；耗时通常较长，识别仍可能出错。"
        "首次解析完整文件并缓存当前 Run，页范围只控制展示；小结果一次返回所有页面，"
        "大结果返回原始页码、正文与 next_read，原样调用本工具续读。"
        "pages[].complete=false表示该页尚未读完，必须完整复制next_read（含char_offset）继续同一页；"
        "不能自行将offset加1，否则会跳过该页剩余正文。"
        "status/summary.coverage=complete只代表供应商解析页覆盖，不代表你已读完正文；"
        "需要全文审查或提取时，has_more=true必须按next_read继续，直到读完所需范围再回答。"
        "同一文档累计等待最多900秒，超时后本Run再次调用不会延长等待或重新提交；不要反复重试。"
        "摘要是页面覆盖概览，不是内容摘要；正文已整理为模型可读格式，普通表格为Markdown，"
        "复杂表格保留精简HTML及合并关系，未作OCR或金额纠错。"
        "char_offset/char_end指向适配后固定正文，不是供应商原始HTML。"
    )
    toolset = FunctionToolset[AgentDeps](id="document-parsing", instructions=(
        description + "不要猜测 file_id 或 document_id。缓存仅本 Run 有效；重复原文件引用命中缓存但默认从头读，"
        "只在 has_more=true 时原样使用 next_read。业务提取应保留原文金额、单位与冲突，不把 partial 当全文完整。"
    ))

    @toolset.tool(description=description)
    async def parse_document(
        ctx: RunContext[AgentDeps], resource: DocumentResource, page_start: int | None = None,
        page_end: int | None = None, limit: int | None = None, offset: int = 1, char_offset: int = 0,
    ) -> DocumentReadResult:
        """解析授权文档，或从当前 Run 的固定可读内容缓存按 Token 预算读取。

        Args:
            resource: 会话附件、已授权 Skill 文件或本工具返回的 document 引用。
            page_start: 原始页范围起点，从 1 开始；仅控制缓存展示，不减少首次供应商解析页数。
            page_end: 原始页范围终点（包含）；省略则读到范围末尾。
            limit: 最多返回的页面数；省略时按当前模型 24000/48000 Token 预算尽量返回。
            offset: 所选页面序列中的起点，从 1 开始；续读必须复制 next_read，不能自行加 1。
            char_offset: 超长单页的 Unicode 字符续读位置，从 0 开始；必须复制 next_read 中的值，否则会遗漏或重复内容。
        """
        started = time.monotonic()
        _validate_range(page_start=page_start, page_end=page_end, limit=limit, offset=offset, char_offset=char_offset)
        if config.mode != "provider" or config.configuration_error or not config.api_key or capability is None:
            raise ToolFailed("文档解析尚未正确配置；请在全局配置 → 常规 → 文档解析中设置供应商和 Key")
        try:
            reference = _RESOURCE.validate_python(resource)
        except ValidationError as exc:
            raise ModelRetry("resource 必须是附件、已授权 Skill 文件或当前 Run 的 document 引用") from exc
        cache = ctx.deps.document_cache
        if cache is None:
            cache = DocumentRunCache()
            ctx.deps.document_cache = cache
        record = None
        if isinstance(reference, DocumentRef):
            record = cache.records.get(reference.document_id)
            if record is None:
                raise ModelRetry("document_id 不属于当前 Run；请使用本工具返回的引用，或重新提供已授权原文件引用")
            reference = record.resource
        resolved = await resolve_file_source(ctx, reference, authorized, tool_name="parse_document")
        mime = (resolved.mime or "").split(";", 1)[0].strip().lower()
        if not capability.supports(resolved.name, mime):
            raise ModelRetry(f"{capability.label} 的当前接入服务不支持此文件格式；支持：{formats}")
        data = await load_file_bytes(ctx, key=resolved.cache_key, loader=resolved.load, failure_label=resolved.name,
                                     max_bytes=min(capability.max_bytes, max_upload_bytes()) if resolved.source == "attachment" else capability.max_bytes,
                                     expected_hash=resolved.content_hash, tool_name="parse_document")
        source_hash = hashlib.sha256(data).hexdigest()
        if len(data) > capability.max_bytes or (resolved.source == "attachment" and len(data) > max_upload_bytes()):
            raise ModelRetry("文件超过当前上传或供应商大小限制")
        if resolved.content_hash and source_hash != resolved.content_hash:
            raise ToolFailed("Skill 资源内容 Hash 与已安装索引不一致")
        hit = record is not None
        if record is not None:
            if record.source_key != resolved.cache_key or record.source_hash != source_hash:
                raise ModelRetry("原文件来源已变化，请重新提供已授权文件引用")
        else:
            source_pages = await asyncio.to_thread(_source_pages, data, mime)
            if capability.max_pages and source_pages and source_pages > capability.max_pages:
                raise ModelRetry(f"文件页数超过 {capability.label} 当前服务的 {capability.max_pages} 页限制")
            source = DocumentSource(resolved.name, mime, data, source_pages)
            context = {"session_id": ctx.deps.session_id, "run_id": ctx.deps.run_id or ctx.run_id,
                       "tool_call_id": ctx.tool_call_id}
            try:
                record, hit = await cache.get_or_parse(
                    source_key=resolved.cache_key, source_hash=source_hash, provider=config.provider, model=config.model,
                    resource=resolved.resource, parse=lambda directory: parse_with_provider(config, source, directory, context=context),
                    estimator=budget.estimator,
                )
            except DocumentParsingError as exc:
                emit("document_read_failed", provider_name=config.provider, error_code=exc.code,
                     elapsed_ms=round((time.monotonic() - started) * 1000), session_id=ctx.deps.session_id,
                     run_id=ctx.deps.run_id or ctx.run_id, tool_call_id=ctx.tool_call_id)
                raise ToolFailed(f"文档解析失败（{exc.code}）：{exc}") from exc
            if not hit:
                stats = record.readable.stats
                emit("document_adaptation_completed", provider_name=config.provider, model_name=config.model,
                     phase="adapt", adaptation_version=stats.version,
                     source_characters=stats.source_characters, adapted_characters=stats.adapted_characters,
                     native_token_count=stats.native_token_count, adapted_token_count=stats.adapted_token_count,
                     table_count=stats.table_count, markdown_table_count=stats.markdown_table_count,
                     compact_html_table_count=stats.compact_html_table_count,
                     adaptation_fallback_count=stats.fallback_count,
                     image_placeholder_count=stats.image_placeholder_count,
                     elapsed_ms=stats.elapsed_ms, **context)
        result = await asyncio.to_thread(read_cached_document, record, budget=budget, page_start=page_start,
                                         page_end=page_end, limit=limit, offset=offset, char_offset=char_offset)
        emit("document_read_completed", provider_name=result.provider, model_name=result.model, phase="page",
             cache_hit=hit, token_count=budget.estimator.text(result.model_dump(mode="json")),
             returned_pages=result.returned_count, has_more=result.has_more, offset=offset, char_offset=char_offset,
             elapsed_ms=round((time.monotonic() - started) * 1000), session_id=ctx.deps.session_id,
             run_id=ctx.deps.run_id or ctx.run_id, tool_call_id=ctx.tool_call_id)
        return result

    return toolset
