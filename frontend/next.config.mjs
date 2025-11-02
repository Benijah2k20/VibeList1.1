/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'i.scdn.co' },   // Spotify artist images
      { protocol: 'https', hostname: 'mosaic.scdn.co' }, // (optional) playlist collages
    ],
  },
};

export default nextConfig;
