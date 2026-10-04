import { useEffect, useId, useRef, type ReactNode } from 'react';

// Review panels preserve the underlying workspace size and live subscriptions.
export function WorkspaceDrawer({
  title,
  wide = false,
  onClose,
  children,
}: {
  title: string;
  wide?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
  }, []);
  return (
    <section
      className={`workspace-drawer ${wide ? 'wide-drawer' : ''}`}
      aria-labelledby={titleId}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !event.defaultPrevented) {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="drawer-heading">
        <h2 id={titleId}>{title}</h2>
        <button
          ref={close}
          onClick={onClose}
          aria-label={`Close ${title.toLowerCase()}`}
        >
          Close
        </button>
      </header>
      <div
        className="drawer-scroll"
        tabIndex={0}
        aria-label={`${title} content`}
      >
        {children}
      </div>
    </section>
  );
}
