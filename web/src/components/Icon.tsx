// Hand-drawn 24x24 stroke icons, one per component kind (no icon library).
const paths: Record<string, JSX.Element> = {
  plc: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M7 4v16M3 9h4M3 14h4" />
      <circle cx="11" cy="9" r="1" />
      <circle cx="15" cy="9" r="1" />
      <path d="M10 14h8M10 17h5" />
    </>
  ),
  gateway: (
    <>
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M7 16.5h.01M11 16.5h.01M8 13V9M16 13V9" />
      <path d="M5 7a10 10 0 0 1 14 0M8 9.5a5.5 5.5 0 0 1 8 0" />
    </>
  ),
  imu: (
    <>
      <rect x="6" y="6" width="12" height="12" rx="1.5" />
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
      <path d="M9.5 14.5 14.5 9.5M14.5 9.5h-3M14.5 9.5v3" />
    </>
  ),
  camera: (
    <>
      <path d="M3 8h3l2-3h8l2 3h3v11H3z" />
      <circle cx="12" cy="13" r="3.5" />
      <path d="M10.5 13h3M12 11.5v3" />
    </>
  ),
  broker: (
    <>
      <circle cx="12" cy="12" r="2.5" />
      <circle cx="5" cy="5" r="2" />
      <circle cx="19" cy="5" r="2" />
      <circle cx="5" cy="19" r="2" />
      <circle cx="19" cy="19" r="2" />
      <path d="M6.5 6.5 10 10M17.5 6.5 14 10M6.5 17.5 10 14M17.5 17.5 14 14" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5.5" rx="7" ry="2.5" />
      <path d="M5 5.5v13c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-13M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5" />
    </>
  ),
  historian: (
    <>
      <ellipse cx="10" cy="5.5" rx="6" ry="2.5" />
      <path d="M4 5.5v11c0 1.4 2.7 2.5 6 2.5M16 5.5V10" />
      <circle cx="17" cy="16" r="4.5" />
      <path d="M17 13.5V16l1.5 1.5" />
    </>
  ),
  api: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M3 8h18M9 12l-2.5 2.5L9 17M15 12l2.5 2.5L15 17" />
    </>
  ),
  "db-ui": (
    <>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M3 8h18" />
      <circle cx="11" cy="14" r="3" />
      <path d="m13.2 16.2 2.8 2.8" />
    </>
  ),
};

export function Icon({ kind, size = 28 }: { kind: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[kind] ?? <rect x="4" y="4" width="16" height="16" rx="2" />}
    </svg>
  );
}
