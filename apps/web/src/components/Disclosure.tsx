'use client';

import { Collapse } from 'antd';
import type { ReactNode } from 'react';

export function Disclosure({
  label,
  headingClassName,
  className,
  variant = 'ghost',
  defaultExpanded,
  expanded,
  onExpandedChange,
  children,
  ...attributes
}: {
  label: ReactNode;
  headingClassName?: string;
  className?: string;
  variant?: 'ghost' | 'outlined';
  defaultExpanded?: boolean;
  expanded?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
  children: ReactNode;
  'aria-label'?: string;
  'data-finding-id'?: string;
}) {
  return (
    <Collapse
      ghost={variant === 'ghost'}
      size={variant === 'outlined' ? 'middle' : 'small'}
      className={['workspace-disclosure', className].filter(Boolean).join(' ')}
      defaultActiveKey={defaultExpanded ? ['body'] : undefined}
      activeKey={expanded === undefined ? undefined : expanded ? ['body'] : []}
      onChange={onExpandedChange ? (keys) => onExpandedChange(keys.includes('body')) : undefined}
      items={[
        {
          key: 'body',
          label,
          children,
          forceRender: true,
          classNames: { title: headingClassName },
        },
      ]}
      {...attributes}
    />
  );
}
