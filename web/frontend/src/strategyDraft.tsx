import { createContext, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { TARGET_STRATEGY_IDS, type StrategyTargetVersion, type StrategyWeights, type TargetStrategyId } from "./types";

const emptyWeights = Object.fromEntries(TARGET_STRATEGY_IDS.map((id) => [id, "0"])) as StrategyWeights;

interface StrategyDraftContextValue {
  weights: StrategyWeights;
  lastSaved: StrategyTargetVersion | null;
  dirty: boolean;
  setWeight: (id: TargetStrategyId, value: string) => void;
  hydrate: (version: StrategyTargetVersion | null) => void;
  saved: (version: StrategyTargetVersion) => void;
  restore: () => void;
  clear: () => void;
}

const StrategyDraftContext = createContext<StrategyDraftContextValue | null>(null);

export function StrategyDraftProvider({ children }: { children: ReactNode }) {
  const [weights, setWeights] = useState<StrategyWeights>(emptyWeights);
  const [lastSaved, setLastSaved] = useState<StrategyTargetVersion | null>(null);
  const [dirty, setDirty] = useState(false);
  const hydrated = useRef(false);

  const value = useMemo<StrategyDraftContextValue>(() => ({
    weights,
    lastSaved,
    dirty,
    setWeight: (id, next) => {
      setWeights((current) => ({ ...current, [id]: next }));
      setDirty(true);
    },
    hydrate: (version) => {
      if (hydrated.current || dirty) return;
      hydrated.current = true;
      setLastSaved(version);
      if (version) setWeights(version.weights);
    },
    saved: (version) => {
      hydrated.current = true;
      setLastSaved(version);
      setWeights(version.weights);
      setDirty(false);
    },
    restore: () => {
      if (lastSaved) setWeights(lastSaved.weights);
      setDirty(false);
    },
    clear: () => {
      hydrated.current = false;
      setWeights(emptyWeights);
      setLastSaved(null);
      setDirty(false);
    }
  }), [dirty, lastSaved, weights]);

  return <StrategyDraftContext.Provider value={value}>{children}</StrategyDraftContext.Provider>;
}

export function useStrategyDraft(): StrategyDraftContextValue {
  const value = useContext(StrategyDraftContext);
  if (!value) throw new Error("StrategyDraftProvider가 필요합니다.");
  return value;
}
