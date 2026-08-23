type Props = {
  className?: string;
  label?: string;
};

export function OperlyMark({ className = "", label = "Operly" }: Props) {
  return <span className={`operly-mark ${className}`.trim()} aria-label={label} role="img"><img src="/operly-logo.png" alt="" /></span>;
}
