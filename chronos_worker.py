from __future__ import annotations

import json
import math
import os
import sys


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def main() -> int:
    payload = json.load(sys.stdin)
    clean = [float(x) for x in payload.get('context', []) if float(x) > 0]
    horizon = max(1, min(32, int(payload.get('prediction_length', 8))))
    if not clean:
        print(json.dumps({'error': 'empty-context'}))
        return 2

    import torch
    from chronos import BaseChronosPipeline

    torch.set_num_threads(max(1, int(os.getenv('CHRONOS_TORCH_THREADS', '1'))))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass

    model_name = os.getenv('CHRONOS_MODEL', 'amazon/chronos-bolt-tiny').strip()
    dtype_name = os.getenv('CHRONOS_TORCH_DTYPE', 'float32').strip().lower()
    dtype = {'float32': torch.float32, 'bfloat16': torch.bfloat16, 'float16': torch.float16}.get(dtype_name, torch.float32)
    pipeline = BaseChronosPipeline.from_pretrained(model_name, device_map='cpu', torch_dtype=dtype)

    with torch.inference_mode():
        context = torch.tensor(clean, dtype=torch.float32)
        quantiles, mean = pipeline.predict_quantiles(
            context,
            prediction_length=horizon,
            quantile_levels=[0.1, 0.5, 0.9],
        )
    q = quantiles.detach().cpu()[0]
    avg = mean.detach().cpu()[0]
    last = clean[-1]
    q10, q50, q90 = float(q[-1, 0]), float(q[-1, 1]), float(q[-1, 2])
    mean_last = float(avg[-1])
    interval = max(1e-9, q90 - q10)
    sigma = max(interval / 2.563, last * 1e-6)
    probability_up = normal_cdf((q50 - last) / sigma) * 100.0
    uncertainty_pct = interval / last * 100.0
    median_return = (q50 / last - 1.0) * 100.0
    result = {
        'provider': 'amazon-chronos-bolt',
        'model': model_name,
        'contextPoints': len(clean),
        'predictionLength': horizon,
        'lastPrice': round(last, 10),
        'forecastPrice': round(q50, 10),
        'meanForecastPrice': round(mean_last, 10),
        'forecastReturnPct': round(median_return, 4),
        'meanReturnPct': round((mean_last / last - 1.0) * 100.0, 4),
        'lowerReturnPct': round((q10 / last - 1.0) * 100.0, 4),
        'upperReturnPct': round((q90 / last - 1.0) * 100.0, 4),
        'probabilityUp': round(max(0.0, min(100.0, probability_up)), 2),
        'probabilityDown': round(max(0.0, min(100.0, 100.0 - probability_up)), 2),
        'uncertaintyPct': round(uncertainty_pct, 4),
        'strength': round(min(100.0, abs(median_return) / max(uncertainty_pct, 0.05) * 50.0), 2),
    }
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
