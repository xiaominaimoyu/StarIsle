import { useId, useState, type InputHTMLAttributes } from "react";

interface FloatInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  /** 校验失败提示(经 aria-describedby 关联,role="alert" 播报) */
  invalid?: boolean;
  errorText?: string;
  /** 密码框的显示/隐藏切换 */
  password?: boolean;
}

/**
 * 下沉式浮动标签输入框(参考稿的签名交互):
 * 标签初始位于输入行内,聚焦或已有值时下沉到输入框底部预留区并缩小加重。
 */
export default function FloatInput({
  label,
  invalid = false,
  errorText,
  password = false,
  className = "",
  id: propId,
  required = false,
  ...rest
}: FloatInputProps) {
  const autoId = useId();
  const id = propId ?? autoId;
  const errorId = `${id}-error`;
  const [showPassword, setShowPassword] = useState(false);
  const [touched, setTouched] = useState(false);

  const showError = invalid && touched && !!errorText;
  const inputType = password ? (showPassword ? "text" : "password") : (rest.type ?? "text");

  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <label
        htmlFor={id}
        className={`relative block h-11 rounded-sm border bg-surface/40 transition-colors duration-200 ${
          showError
            ? "border-danger"
            : invalid
              ? "border-danger/50"
              : "border-border focus-within:border-accent"
        }`}
      >
        <input
          id={id}
          type={inputType}
          required={required}
          aria-label={label}
          className={`peer h-full w-full rounded-sm border-none bg-transparent px-2.5 pb-2 pt-3 text-base text-text outline-none ${password ? "pr-11" : ""}`}
          aria-describedby={showError ? errorId : undefined}
          aria-invalid={invalid || undefined}
          onBlur={(e) => {
            setTouched(true);
            rest.onBlur?.(e);
          }}
          {...rest}
        />
        <span
          aria-hidden="true"
          className={`pointer-events-none absolute left-2.5 top-3 text-sm text-text-dim transition-all duration-200 ease-out
            peer-focus:bottom-1 peer-focus:top-auto peer-focus:text-xs peer-focus:font-semibold
            peer-focus:text-accent
            peer-[:not(:placeholder-shown)]:bottom-1 peer-[:not(:placeholder-shown)]:top-auto
            peer-[:not(:placeholder-shown)]:text-xs peer-[:not(:placeholder-shown)]:font-semibold
            ${invalid && touched ? "peer-focus:text-danger peer-[:not(:placeholder-shown)]:text-danger" : ""}
          `}
        >
          {label}
        </span>
        {password ? (
          <button
            type="button"
            aria-label={showPassword ? "隐藏密码" : "显示密码"}
            className="absolute right-1 top-1 inline-flex h-9 items-center rounded px-2 text-xs text-text-dim hover:text-text focus-ring"
            onClick={() => setShowPassword((v) => !v)}
          >
            {showPassword ? "隐藏" : "显示"}
          </button>
        ) : null}
      </label>
      {showError ? (
        <span id={errorId} role="alert" className="text-xs text-danger">
          {errorText}
        </span>
      ) : null}
    </div>
  );
}
