export type NarrativeQuality = "high" | "medium" | "low" | "reduced";

export interface NarrativeQualitySignals {
  reducedMotion: boolean;
  saveData: boolean;
  viewportWidth: number;
  deviceMemory?: number;
  measuredFps?: number;
}

const QUALITY_RANK: Record<NarrativeQuality, number> = {
  reduced: 0,
  low: 1,
  medium: 2,
  high: 3,
};

/** 只使用能力/偏好信号，不做 UA sniff；运行中测量只能让质量下降。 */
export function selectNarrativeQuality(signals: NarrativeQualitySignals): NarrativeQuality {
  if (signals.reducedMotion) return "reduced";
  if (
    signals.saveData
    || signals.viewportWidth < 480
    || (signals.deviceMemory !== undefined && signals.deviceMemory <= 2)
    || (signals.measuredFps !== undefined && signals.measuredFps < 30)
  ) {
    return "low";
  }
  if (
    signals.viewportWidth >= 960
    && (signals.deviceMemory === undefined || signals.deviceMemory >= 6)
    && (signals.measuredFps === undefined || signals.measuredFps >= 55)
  ) {
    return "high";
  }
  return "medium";
}

export function lowerNarrativeQuality(
  current: NarrativeQuality,
  candidate: NarrativeQuality,
): NarrativeQuality {
  return QUALITY_RANK[candidate] < QUALITY_RANK[current] ? candidate : current;
}
