"""
Constants for SuperTrend Optimizer.

This module contains all constant values used throughout the application.
Unified between Optuna (2.0) and Grid Search (1.1) projects.
"""

from typing import Final

# =============================================================================
# МЕТРИКИ
# =============================================================================

# Единое значение для всех невалидных метрик
# КРИТИЧНО: Использовать ВЕЗДЕ вместо np.nan, None, inf
INVALID_METRIC_VALUE: Final[float] = -999.0

# F-08/F-18: Cap for legitimately extreme metric values (e.g. PF with
# zero losses).  Any objective_value above this is treated as an outlier
# and filtered out during grid/Optuna search.
MAX_VALID_METRIC: Final[float] = 9999.0

# Small epsilon value to avoid division by zero and floating point comparisons
EPS: Final[float] = 1e-12

# Минимальный порог стандартного отклонения для избежания деления на ноль
MIN_STD_THRESHOLD: Final[float] = 1e-8

# Минимальное количество сделок для валидности метрик
DEFAULT_MIN_TRADES_REQUIRED: Final[int] = 3

# Максимальное значение для ratio-метрик (когда знаменатель близок к нулю)
MAX_RATIO_VALUE: Final[float] = 100.0

# =============================================================================
# ВРЕМЕННЫЕ ПАРАМЕТРЫ
# =============================================================================

# Количество торговых дней в году (для дневных данных)
DEFAULT_ANNUALIZATION_FACTOR: Final[int] = 252

# Минимальное количество баров для валидного анализа
MIN_DATA_POINTS: Final[int] = 50

# Рекомендуемое минимальное количество баров
RECOMMENDED_MIN_DATA_POINTS: Final[int] = 200

# Auto warmup calculation constants
DEFAULT_WARMUP_FRACTION: Final[float] = 0.10  # 10% of data length
MIN_AUTO_WARMUP: Final[int] = 100
MAX_AUTO_WARMUP: Final[int] = 400
DEFAULT_MIN_BARS_AFTER_WARMUP: Final[int] = 100

# =============================================================================
# ПАРАМЕТРЫ ДАННЫХ
# =============================================================================

# Поддерживаемые кодировки файлов
SUPPORTED_ENCODINGS: Final[tuple] = ('utf-8', 'cp1251', 'latin-1')

# Поддерживаемые разделители CSV
SUPPORTED_DELIMITERS: Final[tuple] = (';', ',', '\t', '|')

# Обязательные колонки в CSV
REQUIRED_COLUMNS: Final[tuple] = ('open', 'high', 'low', 'close')

# Опциональные колонки для временных меток
DATETIME_COLUMNS: Final[tuple] = ('datetime', 'date', 'time', 'timestamp')

# =============================================================================
# BUCKET PARAMETERS (canonical defaults — single source of truth)
# =============================================================================

# Step for ATR-period bucketing (walk_forward.consensus.atr_bucket_step).
# All pipeline layers must import this constant instead of using local literals.
# Canonical entrypoint: build_aggregated_topk_table in scoring/aggregation.py.
DEFAULT_ATR_BUCKET_STEP: Final[int] = 2

# Step for multiplier bucketing (walk_forward.consensus.mult_bucket_step).
DEFAULT_MULT_BUCKET_STEP: Final[float] = 0.2

# =============================================================================
# ПАРАМЕТРЫ ОПТИМИЗАЦИИ (по умолчанию)
# =============================================================================

# Диапазон ATR Period (UNIFIED)
DEFAULT_ATR_PERIOD_MIN: Final[int] = 5
DEFAULT_ATR_PERIOD_MAX: Final[int] = 55

# Диапазон Multiplier (UNIFIED)
DEFAULT_MULTIPLIER_MIN: Final[float] = 1.5
DEFAULT_MULTIPLIER_MAX: Final[float] = 5.5
DEFAULT_MULTIPLIER_STEP: Final[float] = 0.1

# Метрика оптимизации по умолчанию
DEFAULT_OBJECTIVE_METRIC: Final[str] = "sortino"

# Доступные метрики для оптимизации
AVAILABLE_OBJECTIVE_METRICS: Final[tuple] = ("sortino", "net_profit_simple")

# Режимы торговли
TRADE_MODES: Final[tuple] = ("both", "long", "short")
DEFAULT_TRADE_MODE: Final[str] = "both"

# =============================================================================
# ПАРАМЕТРЫ БЭКТЕСТА (UNIFIED)
# =============================================================================

# Комиссия по умолчанию (0.0235%)
DEFAULT_COMMISSION: Final[float] = 0.000235

# Параметры раннего выхода (UNIFIED)
DEFAULT_EARLY_EXIT_ENABLED: Final[bool] = True
DEFAULT_EARLY_EXIT_MAX_DRAWDOWN: Final[float] = 0.50  # 50%
DEFAULT_EARLY_EXIT_CHECK_BARS: Final[int] = 50

# =============================================================================
# ПАРАМЕТРЫ ВАЛИДАЦИИ
# =============================================================================

# Validation constants
RANGE_LENGTH: Final[int] = 2  # Expected length for range parameters [min, max]
MIN_WARMUP_PERIOD: Final[int] = 0  # Minimum allowed warmup period
MIN_BARS_AFTER_WARMUP_THRESHOLD: Final[int] = 1  # Minimum bars required after warmup
MIN_TRIALS: Final[int] = 1  # Minimum number of optimization trials
MIN_JOBS: Final[int] = 1  # Minimum number of parallel jobs

# Data validation constants
MIN_ARRAY_LENGTH: Final[int] = 2  # Minimum length for price/indicator arrays
MIN_ATR_PERIOD: Final[int] = 2  # Minimum ATR period for trend calculation
MIN_PRICE_VALUE: Final[float] = 1e-12  # Minimum valid price (to avoid division by zero)

# Максимальное изменение цены между барами (для обнаружения выбросов)
DEFAULT_MAX_PRICE_CHANGE_PERCENT: Final[float] = 50.0

# =============================================================================
# ПАРАМЕТРЫ ВЫВОДА
# =============================================================================

# Количество лучших результатов для сохранения
DEFAULT_TOP_K_TRIALS: Final[int] = 100

# Периоды для мультипериодной валидации
DATA_PERIODS: Final[dict] = {
    "100%": 1.0,
    "50%": 0.5,
    "30%": 0.3,
}

# =============================================================================
# ПАРАМЕТРЫ КЭШИРОВАНИЯ
# =============================================================================

# Максимальный размер кэша (количество записей)
MAX_CACHE_SIZE: Final[int] = 500

# Максимальный размер кэша в байтах (100 MB)
MAX_CACHE_SIZE_BYTES: Final[int] = 100 * 1024 * 1024

# =============================================================================
# ЦВЕТОВАЯ СХЕМА EXCEL
# =============================================================================

# Цвета для Excel (формат ARGB без #)
EXCEL_COLOR_GREEN: Final[str] = "C6EFCE"  # Для топ 10%
EXCEL_COLOR_RED: Final[str] = "FFC7CE"    # Для низ 10%
EXCEL_COLOR_YELLOW: Final[str] = "FFEB9C" # Для предупреждений
EXCEL_COLOR_WHITE: Final[str] = "FFFFFF"  # Стандартный фон

# Процентили для цветовой градации
TOP_PERCENTILE: Final[float] = 0.9   # Топ 10%
BOTTOM_PERCENTILE: Final[float] = 0.1  # Низ 10%

# =============================================================================
# УРОВНИ ЛОГИРОВАНИЯ
# =============================================================================

LOG_LEVELS: Final[tuple] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LOG_LEVEL: Final[str] = "INFO"

# =============================================================================
# FILTER REASONS
# =============================================================================

FILTER_REASON_OK:             Final[str] = "ok"
FILTER_REASON_ATR_BELOW_MIN:  Final[str] = "atr_below_min"
FILTER_REASON_ATR_ABOVE_MAX:  Final[str] = "atr_above_max"
FILTER_REASON_ATR_NAN:        Final[str] = "atr_nan"
FILTER_REASON_VOL_BELOW_MIN:  Final[str] = "vol_below_min"
FILTER_REASON_VOL_ABOVE_MAX:  Final[str] = "vol_above_max"
FILTER_REASON_VOL_MA_INVALID: Final[str] = "vol_ma_invalid"
FILTER_REASON_VOL_NAN:        Final[str] = "vol_nan"
FILTER_REASON_BOTH:           Final[str] = "both"

# Amplitude filter reason codes (v1.3)
FILTER_REASON_AMP_WARMUP:          Final[str] = "amp_warmup"
FILTER_REASON_AMP_SEPARATION_FAIL: Final[str] = "amp_separation_fail"
FILTER_REASON_AMP_BELOW_THRESHOLD: Final[str] = "amp_below_threshold"
FILTER_REASON_ATR_FLOOR_BELOW:     Final[str] = "atr_floor_below"

# ZigZag filter reason codes (v2.0)
FILTER_REASON_ZZ_WARMUP:            Final[str] = "zz_warmup"
FILTER_REASON_ZZ_REGIME_OFF:        Final[str] = "zz_regime_off"
FILTER_REASON_ZZ_NOT_ARMED:         Final[str] = "zz_not_armed"
FILTER_REASON_ZZ_ARMED_WAITING:     Final[str] = "zz_armed_waiting"
FILTER_REASON_ZZ_EXPIRED_TIME:      Final[str] = "zz_expired_time"
FILTER_REASON_ZZ_EXPIRED_NEW_PIVOT: Final[str] = "zz_expired_new_pivot"
FILTER_REASON_ZZ_LOCKED_SAME_LEG:   Final[str] = "zz_locked_same_leg"
FILTER_REASON_ZZ_PATHOLOGICAL:      Final[str] = "zz_pathological"

FILTER_REASON_WHITELIST: Final[frozenset] = frozenset({
    FILTER_REASON_OK,
    FILTER_REASON_ATR_BELOW_MIN,
    FILTER_REASON_ATR_ABOVE_MAX,
    FILTER_REASON_ATR_NAN,
    FILTER_REASON_VOL_BELOW_MIN,
    FILTER_REASON_VOL_ABOVE_MAX,
    FILTER_REASON_VOL_MA_INVALID,
    FILTER_REASON_VOL_NAN,
    FILTER_REASON_BOTH,
    # Amplitude filter reasons (v1.3)
    FILTER_REASON_AMP_WARMUP,
    FILTER_REASON_AMP_SEPARATION_FAIL,
    FILTER_REASON_AMP_BELOW_THRESHOLD,
    FILTER_REASON_ATR_FLOOR_BELOW,
    # ZigZag filter reasons (v2.0)
    FILTER_REASON_ZZ_WARMUP,
    FILTER_REASON_ZZ_REGIME_OFF,
    FILTER_REASON_ZZ_NOT_ARMED,
    FILTER_REASON_ZZ_ARMED_WAITING,
    FILTER_REASON_ZZ_EXPIRED_TIME,
    FILTER_REASON_ZZ_EXPIRED_NEW_PIVOT,
    FILTER_REASON_ZZ_LOCKED_SAME_LEG,
    FILTER_REASON_ZZ_PATHOLOGICAL,
})

# Valid filter modes
FILTER_MODES: Final[frozenset] = frozenset({
    "none",
    "volatility",            # deprecated — use amplitude instead
    "volume",
    "volatility_and_volume", # deprecated — use amplitude_and_volume instead
    "amplitude",             # v1.3
    "amplitude_and_volume",  # v1.3
    "zigzag",                # v2.0
    "zigzag_and_volume",     # v2.0
})

