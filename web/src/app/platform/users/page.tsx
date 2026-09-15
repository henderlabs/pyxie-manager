import { apiFetch } from "@/lib/api";
import type { UserAccount } from "@/lib/api";
import { Card, CardTitle, PageHeader } from "@/components/Card";
import UsersTable from "@/components/UsersTable";
import InviteUserForm from "@/components/InviteUserForm";
import { UsersIcon } from "@/components/Icons";

export default async function UsersPage() {
  let users: UserAccount[] = [];
  let forbidden = false;
  try {
    users = await apiFetch<UserAccount[]>("/api/auth/users");
  } catch {
    forbidden = true;
  }

  return (
    <div>
      <PageHeader title="Users" subtitle="Who can sign in, and what they can do" icon={<UsersIcon className="w-5 h-5" />} />
      {forbidden ? (
        <Card>
          <div className="text-sm text-muted">Admins only.</div>
        </Card>
      ) : (
        <>
          <Card className="mb-4">
            <CardTitle>Accounts</CardTitle>
            <UsersTable initial={users} />
          </Card>
          <Card>
            <InviteUserForm />
          </Card>
        </>
      )}
    </div>
  );
}
