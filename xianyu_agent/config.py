from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


def _load_dotenv(path: Path, override: bool = True) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_float_env(name: str) -> Optional[float]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_json_mode: bool
    deepseek_thinking_enabled: bool
    deepseek_reasoning_effort: str
    deepseek_trust_env: bool
    # MiMo/Anthropic compatible API
    mimo_api_key: str
    mimo_base_url: str
    mimo_model: str
    # Per-agent MiMo keys
    mimo_hermes_key: str
    mimo_listing_key: str
    mimo_discovery_key: str
    mimo_order_key: str
    mimo_github_key: str
    mimo_analytics_key: str
    xianyu_adapter: str
    xianyu_cookies: str
    browser_headless: bool
    browser_cdp_url: str
    xianguanjia_base_url: str
    xianguanjia_token: str
    xianguanjia_app_id: str
    xianguanjia_api_key: str
    xianguanjia_create_endpoint: str
    xianguanjia_polish_endpoint: str
    xianguanjia_fulfill_endpoint: str
    feishu_verification_token: str
    # Deprecated: Feishu CLI long connection handles event delivery.
    # This field is only used by FeishuNotifier for outgoing webhook bot messages.
    feishu_webhook_url: str
    image_search_provider: str
    serpapi_key: str
    bing_search_key: str
    allow_external_product_images: bool
    data_dir: Path
    polish_at: str
    server_host: str
    server_port: int
    github_token: str
    github_trust_env: bool
    github_discovery_enabled: bool
    github_discovery_at: str
    github_discovery_queries: str
    github_discovery_min_stars: int
    github_discovery_limit_per_query: int
    market_search_provider: str
    market_search_limit: int
    default_platform_fee_rate: float
    min_profit_amount: float
    min_profit_rate: float
    ask_feishu_on_opportunity: bool
    crawler_max_pages: int
    crawler_max_downloads: int
    crawler_max_file_mb: int
    platform_guard_enabled: bool
    platform_guard_min_seconds_between_actions: int
    platform_guard_max_actions_per_hour: int
    platform_guard_daily_action_limit: int
    platform_guard_pause_on_verification: bool
    browser_edge_path: str
    browser_debug_port: int
    browser_user_data_dir: str
    browser_headless: bool
    openclaw_root: Path
    openclaw_control_center_root: Path
    openclaw_mission_control_root: Path
    openclaw_rl_root: Path
    awesome_openclaw_skills_root: Path
    # Product discovery settings
    discovery_enabled: bool
    discovery_at: str
    discovery_max_pages: int
    discovery_default_min_price: Optional[float]
    discovery_default_max_price: Optional[float]
    # Auto-reply settings
    auto_reply_enabled: bool
    auto_reply_model: str
    auto_reply_human_toggle: str
    # Database settings
    db_path: str

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> "Settings":
        if env_path is None:
            env_path = Path.cwd() / ".env"
        _load_dotenv(env_path, override=False)
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        db_path = os.environ.get("DB_PATH", "").strip() or str(data_dir / "xianyu_data.db")
        browser_debug_port = int(os.environ.get("BROWSER_DEBUG_PORT", "9223"))
        browser_cdp_url = os.environ.get("BROWSER_CDP_URL", "").strip()
        if not browser_cdp_url:
            browser_cdp_url = f"http://127.0.0.1:{browser_debug_port}"
        return cls(
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            deepseek_json_mode=_bool_env("DEEPSEEK_JSON_MODE", True),
            deepseek_thinking_enabled=_bool_env("DEEPSEEK_THINKING_ENABLED", False),
            deepseek_reasoning_effort=os.environ.get("DEEPSEEK_REASONING_EFFORT", "medium"),
            deepseek_trust_env=_bool_env("DEEPSEEK_TRUST_ENV", False),
            # MiMo/Anthropic compatible API
            mimo_api_key=os.environ.get("MIMO_API_KEY", ""),
            mimo_base_url=os.environ.get("MIMO_BASE_URL", "").rstrip("/"),
            mimo_model=os.environ.get("MIMO_MODEL", "mimo-v2.5-pro"),
            # Per-agent MiMo keys (fallback to default MIMO_API_KEY)
            mimo_hermes_key=os.environ.get("MIMO_HERMES_KEY", "") or os.environ.get("MIMO_API_KEY", ""),
            mimo_listing_key=os.environ.get("MIMO_LISTING_KEY", "") or os.environ.get("MIMO_API_KEY", ""),
            mimo_discovery_key=os.environ.get("MIMO_DISCOVERY_KEY", "") or os.environ.get("MIMO_API_KEY", ""),
            mimo_order_key=os.environ.get("MIMO_ORDER_KEY", "") or os.environ.get("MIMO_API_KEY", ""),
            mimo_github_key=os.environ.get("MIMO_GITHUB_KEY", "") or os.environ.get("MIMO_API_KEY", ""),
            mimo_analytics_key=os.environ.get("MIMO_ANALYTICS_KEY", "") or os.environ.get("MIMO_API_KEY", ""),
            xianyu_adapter=os.environ.get("XIANYU_ADAPTER", "stub").lower(),
            xianyu_cookies=os.environ.get("XIANYU_COOKIES", ""),
            xianguanjia_base_url=os.environ.get("XIANGUANJIA_BASE_URL", "").rstrip("/"),
            xianguanjia_token=os.environ.get("XIANGUANJIA_TOKEN", ""),
            xianguanjia_app_id=os.environ.get("XIANGUANJIA_APP_ID", "")
            or os.environ.get("XIANGUANJIA_APP_KEY", ""),
            xianguanjia_api_key=os.environ.get("XIANGUANJIA_API_KEY", "")
            or os.environ.get("XIANGUANJIA_APP_SECRET", ""),
            xianguanjia_create_endpoint=os.environ.get("XIANGUANJIA_CREATE_ENDPOINT", "/api/listings"),
            xianguanjia_polish_endpoint=os.environ.get(
                "XIANGUANJIA_POLISH_ENDPOINT", "/api/listings/{listing_id}/polish"
            ),
            xianguanjia_fulfill_endpoint=os.environ.get(
                "XIANGUANJIA_FULFILL_ENDPOINT", "/api/orders/{order_id}/fulfill"
            ),
            feishu_verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
            feishu_webhook_url=os.environ.get("FEISHU_WEBHOOK_URL", ""),
            image_search_provider=os.environ.get("IMAGE_SEARCH_PROVIDER", "none").lower(),
            serpapi_key=os.environ.get("SERPAPI_KEY", ""),
            bing_search_key=os.environ.get("BING_SEARCH_KEY", ""),
            allow_external_product_images=_bool_env("ALLOW_EXTERNAL_PRODUCT_IMAGES", False),
            data_dir=data_dir,
            polish_at=os.environ.get("POLISH_AT", "09:30"),
            server_host=os.environ.get("SERVER_HOST", "127.0.0.1"),
            server_port=int(os.environ.get("SERVER_PORT", "8788")),
            github_token=os.environ.get("GITHUB_TOKEN", ""),
            github_trust_env=_bool_env("GITHUB_TRUST_ENV", True),
            github_discovery_enabled=_bool_env("GITHUB_DISCOVERY_ENABLED", True),
            github_discovery_at=os.environ.get("GITHUB_DISCOVERY_AT", "10:30"),
            github_discovery_queries=os.environ.get(
                "GITHUB_DISCOVERY_QUERIES",
                "ai agent,agent framework,automation agent,chatbot,workflow automation",
            ),
            github_discovery_min_stars=int(os.environ.get("GITHUB_DISCOVERY_MIN_STARS", "500")),
            github_discovery_limit_per_query=int(os.environ.get("GITHUB_DISCOVERY_LIMIT_PER_QUERY", "5")),
            market_search_provider=os.environ.get("MARKET_SEARCH_PROVIDER", "none").lower(),
            market_search_limit=int(os.environ.get("MARKET_SEARCH_LIMIT", "5")),
            default_platform_fee_rate=float(os.environ.get("DEFAULT_PLATFORM_FEE_RATE", "0.08")),
            min_profit_amount=float(os.environ.get("MIN_PROFIT_AMOUNT", "20")),
            min_profit_rate=float(os.environ.get("MIN_PROFIT_RATE", "0.30")),
            ask_feishu_on_opportunity=_bool_env("ASK_FEISHU_ON_OPPORTUNITY", True),
            crawler_max_pages=int(os.environ.get("CRAWLER_MAX_PAGES", "3")),
            crawler_max_downloads=int(os.environ.get("CRAWLER_MAX_DOWNLOADS", "5")),
            crawler_max_file_mb=int(os.environ.get("CRAWLER_MAX_FILE_MB", "200")),
            platform_guard_enabled=_bool_env("PLATFORM_GUARD_ENABLED", False),
            platform_guard_min_seconds_between_actions=int(
                os.environ.get("PLATFORM_GUARD_MIN_SECONDS_BETWEEN_ACTIONS", "0")
            ),
            platform_guard_max_actions_per_hour=int(os.environ.get("PLATFORM_GUARD_MAX_ACTIONS_PER_HOUR", "999999")),
            platform_guard_daily_action_limit=int(os.environ.get("PLATFORM_GUARD_DAILY_ACTION_LIMIT", "999999")),
            platform_guard_pause_on_verification=_bool_env("PLATFORM_GUARD_PAUSE_ON_VERIFICATION", False),
            browser_edge_path=os.environ.get(
                "BROWSER_EDGE_PATH",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            ),
            browser_debug_port=browser_debug_port,
            browser_user_data_dir=os.environ.get("BROWSER_USER_DATA_DIR", r"E:\xianyu-agent-browser-profile"),
            browser_headless=_bool_env("BROWSER_HEADLESS", False),
            browser_cdp_url=browser_cdp_url,
            openclaw_root=Path(os.environ.get("OPENCLAW_ROOT", r"E:\openclaw\openclaw-main")),
            openclaw_control_center_root=Path(
                os.environ.get(
                    "OPENCLAW_CONTROL_CENTER_ROOT",
                    r"E:\openclaw-control-center-main\openclaw-control-center-main",
                )
            ),
            openclaw_mission_control_root=Path(
                os.environ.get(
                    "OPENCLAW_MISSION_CONTROL_ROOT",
                    r"E:\openclaw-mission-control-master\openclaw-mission-control-master",
                )
            ),
            openclaw_rl_root=Path(os.environ.get("OPENCLAW_RL_ROOT", r"E:\OpenClaw-RL-main\OpenClaw-RL-main")),
            awesome_openclaw_skills_root=Path(
                os.environ.get("AWESOME_OPENCLAW_SKILLS_ROOT", r"E:\awesome-openclaw-skills-main\awesome-openclaw-skills-main")
            ),
            # Product discovery settings
            discovery_enabled=_bool_env("DISCOVERY_ENABLED", True),
            discovery_at=os.environ.get("DISCOVERY_AT", "11:00"),
            discovery_max_pages=int(os.environ.get("DISCOVERY_MAX_PAGES", "3")),
            discovery_default_min_price=_optional_float_env("DISCOVERY_DEFAULT_MIN_PRICE"),
            discovery_default_max_price=_optional_float_env("DISCOVERY_DEFAULT_MAX_PRICE"),
            # Auto-reply settings
            auto_reply_enabled=_bool_env("AUTO_REPLY_ENABLED", False),
            auto_reply_model=os.environ.get("AUTO_REPLY_MODEL", ""),
            auto_reply_human_toggle=os.environ.get("AUTO_REPLY_HUMAN_TOGGLE", "。"),
            # Database settings
            db_path=db_path,
        )

    def ensure_dirs(self) -> None:
        (self.data_dir / "drafts").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "outbox").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "research").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "opportunities").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "products").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "crawls").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "guard").mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
