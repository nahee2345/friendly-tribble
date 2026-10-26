/** User-facing label must fail closed when server trade-control state is absent. */
export type PaperControlDisplay = {
  label:string;
  action:string;
  nextPaused:boolean;
  canEnableAutomatedReview:boolean;
};
export function paperControlDisplay(paused:boolean|null):PaperControlDisplay {
  if(paused===true) {
    return {
      label:'가상 자동주문 중지',
      action:'가상 자동매매 시작 허용',
      nextPaused:false,
      canEnableAutomatedReview:true
    };
  }
  if(paused===false) {
    return {
      label:'가상 자동주문 허용 · 안전검사 별도 적용',
      action:'긴급 중지',
      nextPaused:true,
      canEnableAutomatedReview:false
    };
  }
  return {
    label:'상태 확인 불가 · 거래 실행 보류',
    action:'긴급 중지 요청',
    nextPaused:true,
    canEnableAutomatedReview:false
  };
}
