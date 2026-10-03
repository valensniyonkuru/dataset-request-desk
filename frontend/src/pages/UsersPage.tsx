import { type FormEvent, useState } from "react";
import { type AdminUser, api, type Role, type UserChanges } from "../api";
import { useAuth } from "../auth";
import { ErrorMessage, messageOf, SuccessMessage, useLoad } from "../ui";

const ROLES: Role[] = ["client", "operator", "admin"];

/** Admin only. The server enforces the guard rails; their messages are shown as they come. */
export default function UsersPage() {
  const { user: me } = useAuth();
  const users = useLoad(() => api.listUsers(), []);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function update(target: AdminUser, changes: UserChanges, doneMessage: string) {
    setBusyId(target.id);
    setError(null);
    setSuccess(null);
    try {
      await api.updateUser(target.id, changes);
      setSuccess(doneMessage);
      users.reload();
    } catch (failure) {
      // e.g. 409 "You cannot demote or deactivate yourself". The select snaps back,
      // because it always shows the role the server last returned.
      setError(messageOf(failure));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section>
      <h1>Users</h1>
      <ErrorMessage message={error} />
      <SuccessMessage message={success} />

      {users.error !== null && <ErrorMessage message={messageOf(users.error)} />}
      {users.data === null && users.error === null && <p>Loading...</p>}
      {users.data !== null && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Organisation</th>
                <th scope="col">Active</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((target) => {
                const isMe = target.id === me?.id;
                return (
                  <tr key={target.id}>
                    <td>{target.name}</td>
                    <td>{target.email}</td>
                    <td>
                      <select
                        aria-label={`Role of ${target.email}`}
                        value={target.role}
                        disabled={busyId === target.id}
                        onChange={(e) =>
                          update(target, { role: e.target.value as Role }, `${target.email} is now ${e.target.value}.`)
                        }
                      >
                        {ROLES.map((role) => (
                          <option key={role} value={role}>
                            {role}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>{target.organisation ?? "—"}</td>
                    <td>{target.is_active ? "Yes" : "No"}</td>
                    <td className="row-actions">
                      <button
                        type="button"
                        disabled={busyId === target.id || isMe}
                        title={isMe ? "You cannot deactivate yourself" : undefined}
                        onClick={() =>
                          update(
                            target,
                            { is_active: !target.is_active },
                            `${target.email} is now ${target.is_active ? "deactivated" : "active"}.`,
                          )
                        }
                      >
                        {target.is_active ? "Deactivate" : "Reactivate"}
                      </button>
                      <ResetPassword
                        email={target.email}
                        disabled={busyId === target.id}
                        onSave={(password) => update(target, { password }, `New password set for ${target.email}.`)}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <CreateUserForm
        onCreated={(created) => {
          setSuccess(`Created ${created.email}.`);
          users.reload();
        }}
      />
    </section>
  );
}

function ResetPassword({
  email,
  disabled,
  onSave,
}: {
  email: string;
  disabled: boolean;
  onSave: (password: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");

  if (!open) {
    return (
      <button type="button" disabled={disabled} onClick={() => setOpen(true)}>
        Reset password
      </button>
    );
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    await onSave(password);
    setPassword("");
    setOpen(false);
  }

  return (
    <form className="inline-form" onSubmit={save}>
      <label>
        <span className="visually-hidden">New password for {email}</span>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          required
          placeholder="New password (8+)"
          autoComplete="new-password"
        />
      </label>
      <button type="submit" disabled={disabled}>
        Save
      </button>
      <button type="button" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </form>
  );
}

function CreateUserForm({ onCreated }: { onCreated: (created: AdminUser) => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("client");
  const [organisation, setOrganisation] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createUser({ email, name, role, organisation: organisation.trim() || null, password });
      onCreated(created);
      setEmail("");
      setName("");
      setOrganisation("");
      setPassword("");
    } catch (failure) {
      setError(messageOf(failure)); // e.g. 409 "A user with this email already exists"
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="create-user-title">
      <h2 id="create-user-title">Create a user</h2>
      <form className="form" onSubmit={submit}>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label>
          Organisation{role === "client" ? " (required for clients)" : " (optional)"}
          <input value={organisation} onChange={(e) => setOrganisation(e.target.value)} required={role === "client"} />
        </label>
        <label>
          Password (at least 8 characters)
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
            autoComplete="new-password"
          />
        </label>
        <ErrorMessage message={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Creating..." : "Create user"}
        </button>
      </form>
    </section>
  );
}
