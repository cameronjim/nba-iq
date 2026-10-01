import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { RegisterPage } from '../../src/pages/RegisterPage';
import { LoginPage } from '../../src/pages/LoginPage';
import { ResetPasswordPage } from '../../src/pages/ResetPasswordPage';

const openGooglePopup = vi.fn();

vi.mock('@react-oauth/google', () => ({
  useGoogleLogin: () => openGooglePopup,
}));

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>();
  return {
    ...actual,
    register: vi.fn(),
    login: vi.fn(),
    resetPassword: vi.fn(),
  };
});

const { register, login, resetPassword } = await import('../../src/api/client');

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Google sign-in button', () => {
  it('renders as a themed in-app button and opens the google popup on click', async () => {
    // arrange
    render(<MemoryRouter><LoginPage onLogin={vi.fn()} /></MemoryRouter>);
    const user = userEvent.setup();

    // act
    await user.click(screen.getByRole('button', { name: 'Continue with Google' }));

    // assert
    expect(openGooglePopup).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Use a different Google account')).not.toBeInTheDocument();
  });

  it('offers google sign-up on the register page', () => {
    // arrange + act
    render(<MemoryRouter><RegisterPage onRegister={vi.fn()} /></MemoryRouter>);

    // assert
    expect(screen.getByRole('button', { name: 'Sign up with Google' })).toBeInTheDocument();
  });
});

describe('RegisterPage', () => {
  it('links to the Terms and Privacy Policy under the submit button', () => {
    // arrange + act
    render(<MemoryRouter><RegisterPage onRegister={vi.fn()} /></MemoryRouter>);

    // assert
    expect(screen.getByText(/By creating an account you agree to the/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/terms');
    expect(screen.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute('href', '/privacy');
  });

  it('shows plain text when the password meets the rules', async () => {
    // arrange
    render(<MemoryRouter><RegisterPage onRegister={vi.fn()} /></MemoryRouter>);
    const user = userEvent.setup();

    // act
    await user.type(screen.getByLabelText('Password'), 'Hardwood1');

    // assert
    expect(screen.getByText('Meets the password rules')).toBeInTheDocument();
  });

  it('changes the button label while the account is being created', async () => {
    // arrange
    vi.mocked(register).mockReturnValue(new Promise(() => {}));
    render(<MemoryRouter><RegisterPage onRegister={vi.fn()} /></MemoryRouter>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Username'), 'cj');
    await user.type(screen.getByLabelText('Email'), 'cj@example.com');
    await user.type(screen.getByLabelText('Password'), 'Hardwood1');
    await user.type(screen.getByLabelText('Confirm password'), 'Hardwood1');

    // act
    await user.click(screen.getByRole('button', { name: 'Create Account' }));

    // assert
    const pending = await screen.findByRole('button', { name: 'Creating account' });
    expect(pending).toBeDisabled();
    expect(document.querySelector('.loading-spinner')).toBeNull();
  });
});

describe('LoginPage', () => {
  it('changes the button label while signing in', async () => {
    // arrange
    vi.mocked(login).mockReturnValue(new Promise(() => {}));
    render(<MemoryRouter><LoginPage onLogin={vi.fn()} /></MemoryRouter>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Username or email'), 'cj');
    await user.type(screen.getByLabelText('Password'), 'Hardwood1');

    // act
    await user.click(screen.getByRole('button', { name: 'Sign In' }));

    // assert
    expect(await screen.findByRole('button', { name: 'Signing in' })).toBeDisabled();
  });
});

describe('ResetPasswordPage', () => {
  it('changes the button label while the new password is saved', async () => {
    // arrange
    vi.mocked(resetPassword).mockReturnValue(new Promise(() => {}));
    render(
      <MemoryRouter initialEntries={['/reset-password?token=abc']}>
        <ResetPasswordPage />
      </MemoryRouter>
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('New password'), 'Hardwood1');
    await user.type(screen.getByLabelText('Confirm password'), 'Hardwood1');
    expect(screen.getByText('Meets the password rules')).toBeInTheDocument();

    // act
    await user.click(screen.getByRole('button', { name: 'Update password' }));

    // assert
    expect(await screen.findByRole('button', { name: 'Saving' })).toBeDisabled();
  });
});
