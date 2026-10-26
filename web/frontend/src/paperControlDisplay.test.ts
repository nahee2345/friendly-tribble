import {describe,expect,it} from 'vitest';
import {paperControlDisplay} from './paperControlDisplay';

describe('PAPER user stop/resume status is always truthful',()=>{
  it('does not show allowed when state is unknown or server fetch failed',()=>{
    const state=paperControlDisplay(null);
    expect(state.label).toContain('상태 확인 불가');
    expect(state.label).toContain('거래 실행 보류');
    expect(state.label).not.toContain('허용');
    expect(state.nextPaused).toBe(true);
    expect(state.action).toContain('긴급 중지');
    expect(state.canEnableAutomatedReview).toBe(false);
  });
  it('confirmed paused permits explicit resume only',()=>{
    const state=paperControlDisplay(true);
    expect(state.label).toContain('중지');
    expect(state.nextPaused).toBe(false);
    expect(state.canEnableAutomatedReview).toBe(true);
  });
  it('confirmed unpaused offers emergency stop rather than resume',()=>{
    const state=paperControlDisplay(false);
    expect(state.action).toBe('긴급 중지');
    expect(state.nextPaused).toBe(true);
    expect(state.canEnableAutomatedReview).toBe(false);
  });
});
