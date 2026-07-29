// 켬/끔 토글. 채운 버튼 대신 네이티브 <input type="checkbox">를 스위치 모양으로 입힌다 —
// 켬/끔은 상태 4단(정상/주의/경고/위험)이 아니므로 상태색을 쓸 자리가 아니고, 표 안에
// 채운 버튼을 세로로 쌓으면 그 열이 화면의 주 동작보다 무거워진다.
// 켜짐/꺼짐은 색이 아니라 노브의 **위치**와 트랙의 **잉크 농도**로 말하고, 글자 라벨이
// 항상 옆에 붙는다 — 색을 못 보는 경우에도 같은 결론에 도달할 수 있어야 한다.
export default function Switch({
  checked,
  disabled,
  onChange,
  label,
  ariaLabel,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
  /** 스위치 옆에 보이는 글자. 색 단독으로 상태를 말하지 않기 위한 필수 채널이다. */
  label: string;
  ariaLabel?: string;
}) {
  return (
    <label className="switch">
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        aria-label={ariaLabel}
        onChange={onChange}
      />
      <span aria-hidden="true" className="switch__track">
        <span className="switch__knob" />
      </span>
      {/* min-w — 켜짐/꺼짐 글자 수가 달라도(2자/3자) 폭이 고정되어 토글할 때마다
          오른쪽 열(동작 버튼들)이 좌우로 밀리지 않는다. */}
      <span className="min-w-[2.5rem] text-xs font-medium text-ink-light">{label}</span>
    </label>
  );
}
