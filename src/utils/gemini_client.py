"""
gemini_client.py
----------------
Merkezi Gemini API istemcisi. 3 anahtar ile otomatik key rotation yapar.
Fallback sırası: GEMINI_API_KEY → GEMINI_API_KEY_1 → GEMINI_API_KEY_2

Kullanım:
    from src.utils.gemini_client import get_gemini_client, generate_with_rotation
    
    content = generate_with_rotation(prompt="...", model="gemini-2.0-flash-lite")
"""

import os
import time
from typing import Optional

# ─── Key listesini ortam değişkenlerinden oku ───────────────────────────────
def _load_keys() -> list[str]:
    keys = []
    for name in ["GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2"]:
        k = os.getenv(name, "").strip()
        if k:
            keys.append(k)
    if not keys:
        raise ValueError(
            "Hiç Gemini API anahtarı bulunamadı. "
            "GEMINI_API_KEY, GEMINI_API_KEY_1 veya GEMINI_API_KEY_2 env değişkenlerinden "
            "en az birini tanımlayın."
        )
    return keys


# ─── Tek bir generate çağrısı (key döngüsüyle) ──────────────────────────────
def generate_with_rotation(
    prompt: str,
    model: Optional[str] = None,
    retry_delay: float = 5.0,
    **kwargs
) -> str:
    """
    Verilen prompt'u Gemini API'ye gönderir.
    429 / ResourceExhausted hatası alınırsa sıradaki anahtara geçer.
    Tüm anahtarlar tükenirse son hatayı fırlatır.

    Args:
        prompt: Gönderilecek metin.
        model: Kullanılacak Gemini model adı (varsayılan: GEMINI_MODEL env değişkeni).
        retry_delay: Anahtar geçişleri arasında beklenecek saniye.

    Returns:
        Modelin ürettiği metin yanıtı.
    """
    from google import genai as _sdk

    model_name = model or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    keys = _load_keys()
    last_error: Exception | None = None

    for idx, key in enumerate(keys):
        try:
            client = _sdk.Client(api_key=key)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                **kwargs
            )
            if idx > 0:
                print(f"[KeyRotation] Anahtar #{idx + 1} başarılı ({model_name}).")
            return response.text.strip()

        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = any(
                token in err_str
                for token in ["429", "resourceexhausted", "quota", "rate_limit", "rateerror"]
            )
            if is_rate_limit:
                print(
                    f"[KeyRotation] ⚠️  Anahtar #{idx + 1} limit doldu "
                    f"({type(e).__name__}). "
                    + (f"Anahtar #{idx + 2}'ye geçiliyor..." if idx + 1 < len(keys) else "Başka anahtar yok!")
                )
                last_error = e
                time.sleep(retry_delay)
                continue  # sonraki key
            else:
                # Rate-limit dışı hata → direkt fırlat
                raise

    raise RuntimeError(
        f"Tüm Gemini API anahtarları tükendi veya kullanılamıyor. "
        f"Son hata: {last_error}"
    )


# ─── Vertex AI / AI Studio seçici istemci ────────────────────────────────────
def get_gemini_client():
    """
    USE_VERTEX_AI=True ise Vertex AI istemcisi, aksi halde
    API anahtarı olan ilk key ile istemci döndürür.
    (generate_with_rotation kullanmak her zaman tercih edilmelidir.)
    """
    from google import genai as _sdk

    use_vertex = os.getenv("USE_VERTEX_AI", "False").lower() == "true"
    if use_vertex:
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not project:
            raise ValueError("USE_VERTEX_AI=True ama GOOGLE_CLOUD_PROJECT tanımlanmamış.")
        if credentials and os.path.exists(credentials):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials
        return _sdk.Client(vertexai=True, project=project, location=location)

    # AI Studio: ilk geçerli anahtarı kullan
    key = _load_keys()[0]
    return _sdk.Client(api_key=key)
