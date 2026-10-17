import { useCallback, useEffect, useState } from "react";

export interface LoadState<T> {
  data: T | null;
  loading: boolean;
  error: string;
  reload: () => void;
}

export function useLoad<T>(loader: () => Promise<T>, dependencies: readonly unknown[] = []): LoadState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    loader()
      .then((value) => { if (active) setData(value); })
      .catch(() => { if (active) { setData(null); setError("자료를 불러오지 못했습니다."); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // loader를 화면 렌더마다 새 요청으로 취급하지 않기 위해 호출자가 명시한 의존성만 사용한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision, ...dependencies]);

  return { data, loading, error, reload };
}
