"""Image discovery is text-only; pixels are read only for explicitly selected IDs."""

from io import BytesIO

import httpx
from mcp.server.fastmcp import Image
from mcp.types import CallToolResult, TextContent
from starlette.concurrency import run_in_threadpool

from cloverboard.server import mcp_models as output
from cloverboard.server.mcp_helpers import READ_ERRORS, failure
from cloverboard.server.sources import SourceRequestError, SourceUnavailable, split_run_id
from cloverboard.server.thumbnails import InvalidCrop, encode_preview, thumbnail_cache
from cloverboard.storage.paths import UnsafeLogPathError
from cloverboard.storage.protocols import FileAsset, RunNotFound, StorageReader

_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_IMAGE_TYPES = {"image/png", "image/jpeg"}


def list_image_names(reader: StorageReader | None, run_id: str, name: str, limit: int, offset: int) -> output.ImageNames:
    split_run_id(run_id)
    try:
        if reader is None:
            raise RunNotFound()
        names = [item for item in reader.image_names(run_id) if name.casefold() in item.name.casefold()]
    except READ_ERRORS as error:
        return output.ImageNames(run_id=run_id, names=[], total=0, next_offset=None, error=failure(error))
    names.sort(key=lambda item: item.name)
    page = names[offset:offset + limit]
    return output.ImageNames(run_id=run_id, names=page, total=len(names),
                             next_offset=offset + len(page) if offset + len(page) < len(names) else None)


def list_images(reader: StorageReader | None, run_id: str, name: str, step: int | None,
                limit: int, offset: int, include: list[str] | None) -> output.ImageList:
    split_run_id(run_id)
    try:
        if reader is None:
            raise RunNotFound()
        page = reader.image_page(run_id, name, step, limit, offset)
    except READ_ERRORS as error:
        return output.ImageList(run_id=run_id, name=name, step=None, images=[], total=0, next_offset=None, error=failure(error))
    images = []
    for image in page.images:
        fields = {}
        if "caption" in (include or []):
            fields["caption"] = image.caption[:200]
            if len(image.caption) > 200:
                fields["caption_truncated"] = True
        images.append(output.ImageItem(image_id=image.image_id, width=image.width, height=image.height, **fields))
    return output.ImageList(run_id=run_id, name=name, step=page.step, images=images, total=page.total, next_offset=page.next_offset)


async def _source_image(asset: FileAsset) -> bytes:
    """Read only the configured source's asset URL; never follow redirects or expose credentials."""
    headers = {"Authorization": f"Bearer {asset.token}"} if asset.token else {}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", asset.url, headers=headers) as response:
                if response.status_code == 404:
                    raise FileNotFoundError()
                if response.status_code != 200:
                    raise SourceUnavailable("Source image is unavailable")
                if response.headers.get("content-type", "").split(";")[0].strip().lower() not in _IMAGE_TYPES:
                    raise ValueError("Asset is not a supported image")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > _MAX_SOURCE_BYTES:
                    raise ValueError("Source image exceeds the byte limit")
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(content) + len(chunk) > _MAX_SOURCE_BYTES:
                        raise ValueError("Source image exceeds the byte limit")
                    content.extend(chunk)
                return bytes(content)
    except httpx.HTTPError as error:
        raise SourceUnavailable("Source image is unavailable") from error


async def get_images(reader: StorageReader | None, items: list[output.ImageRequest], max_side: int) -> CallToolResult:
    """Return bounded PNGs and metadata in request order; failed items do not drop good ones."""
    results, images = [], []
    for item in items:
        identity = {"run_id": item.run_id, "image_id": item.image_id}
        if item.crop is not None:
            identity["crop"] = item.crop
        try:
            split_run_id(item.run_id)
            if reader is None:
                raise RunNotFound()
            # Fetch the original for crops/detail, never crop an already reduced preview.
            asset = await run_in_threadpool(reader.get_asset, item.run_id, item.image_id)
            if asset is None:
                raise FileNotFoundError()
            if asset.path:
                if asset.media_type not in _IMAGE_TYPES:
                    raise ValueError("Asset is not a supported image")
                preview = await run_in_threadpool(thumbnail_cache.get, asset.path, max_side, item.crop)
            elif asset.url:
                content = await _source_image(asset)
                preview = await run_in_threadpool(encode_preview, BytesIO(content), max_side, item.crop)
            else:
                raise FileNotFoundError()
            images.append(Image(data=preview.data, format="png").to_image_content())
            results.append(output.LoadedImage(**identity, image_index=len(images),
                           original_size=preview.original_size, returned_size=preview.returned_size))
            continue
        except RunNotFound as error:
            problem = failure(error)
        except FileNotFoundError:
            problem = output.Failure(code="image_not_found", message="Image not found")
        except UnsafeLogPathError:
            problem = output.Failure(code="path_not_allowed", message="Asset path is not allowed")
        except InvalidCrop:
            problem = output.Failure(code="invalid_crop", message="Crop must stay within the original image")
        except ValueError:
            problem = output.Failure(code="invalid_image", message="Unsupported, oversized or invalid image")
        except (SourceUnavailable, SourceRequestError) as error:
            problem = failure(error)
        except READ_ERRORS:
            problem = output.Failure(code="image_unavailable", message="Image storage is unavailable")
        results.append(output.ImageError(**identity, error=problem))
    data = output.ImagesRead(items=results)
    return CallToolResult(content=[TextContent(type="text", text=data.model_dump_json()), *images],
                          structuredContent=data.model_dump(mode="json"), isError=not images)
