import { Inter } from 'next/font/google';
import type { Metadata } from 'next';
import { Provider } from '@/components/provider';
import './global.css';

const inter = Inter({
  subsets: ['latin'],
});

const description =
  'OpenRouter for Shipping: a fully open-source, MIT-licensed Python library for ' +
  'rating and purchasing through Shippo, ShipStation and Easyship.';

export const metadata: Metadata = {
  metadataBase: new URL('https://sameerkumar18.github.io/shipzil/'),
  title: {
    default: 'shipzil: OpenRouter for Shipping',
    template: '%s · shipzil',
  },
  description,
  applicationName: 'shipzil',
  keywords: [
    'multi-carrier shipping',
    'shipping api redundancy',
    'parcel api',
    'logistics gateway',
    'python shipping library',
    'shippo',
    'shipstation',
    'easyship',
    'shipping labels',
    'multi-carrier',
  ],
  openGraph: {
    type: 'website',
    siteName: 'shipzil',
    title: 'shipzil: OpenRouter for Shipping',
    description,
  },
  twitter: {
    card: 'summary_large_image',
    title: 'shipzil: OpenRouter for Shipping',
    description,
  },
};

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className={inter.className} suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
