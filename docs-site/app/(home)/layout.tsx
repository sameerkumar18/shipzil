import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { baseOptions } from '@/lib/layout.shared';

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <HomeLayout
      {...baseOptions()}
      links={[
        { text: 'Quickstart', url: '/docs/quickstart' },
        { text: 'Concepts', url: '/docs/concepts' },
        { text: 'Providers', url: '/docs/providers' },
        { text: 'Roadmap', url: '/docs/roadmap' },
      ]}
    >
      {children}
    </HomeLayout>
  );
}
