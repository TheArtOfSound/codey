/** @type {import('next').NextConfig} */
const isGitHubPages = process.env.NEXT_PUBLIC_GITHUB_PAGES === "true";
const repoBasePath = "/codey";

const nextConfig = {
  reactStrictMode: true,
  output: isGitHubPages ? "export" : undefined,
  basePath: isGitHubPages ? repoBasePath : undefined,
  assetPrefix: isGitHubPages ? `${repoBasePath}/` : undefined,
  trailingSlash: isGitHubPages ? true : undefined,
  images: {
    unoptimized: isGitHubPages,
    remotePatterns: [
      {
        protocol: "https",
        hostname: "avatars.githubusercontent.com",
      },
    ],
  },
};

if (!isGitHubPages) {
  nextConfig.rewrites = async function rewrites() {
    const apiUrl =
      process.env.NEXT_SERVER_API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://localhost:8000";

    return [
      {
        source: "/api/proxy/:path*",
        destination: `${apiUrl}/:path*`,
      },
    ];
  };
}

module.exports = nextConfig;
