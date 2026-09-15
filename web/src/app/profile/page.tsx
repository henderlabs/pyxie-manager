import { apiFetch } from "@/lib/api";
import type { Me } from "@/lib/api";
import { Card, PageHeader } from "@/components/Card";
import ProfileForm from "@/components/ProfileForm";
import { UsersIcon } from "@/components/Icons";

export default async function ProfilePage() {
  const me = await apiFetch<Me>("/api/auth/me");

  return (
    <div>
      <PageHeader title="My Profile" subtitle="Your own account -- display name and password" icon={<UsersIcon className="w-5 h-5" />} />
      <Card>
        <ProfileForm me={me} />
      </Card>
    </div>
  );
}
