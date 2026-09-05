import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { Logo } from '@/components/logo';
import { gitConfig } from './shared';

/**
 * Shared chrome. `links` is deliberately empty: the docs layout renders them above
 * the page tree, where they duplicated Quickstart and Providers. The marketing
 * layout adds its own — see `app/(home)/layout.tsx`.
 */
export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: <Logo />,
    },
    githubUrl: `https://github.com/${gitConfig.user}/${gitConfig.repo}`,
  };
}
