from django import forms
from django.contrib.auth import get_user_model
from accounts.models import Profile, Wallet
from matches.models import Match

User = get_user_model()

class UserEditForm(forms.ModelForm):
    # Profile fields
    full_name = forms.CharField(max_length=200, required=False)
    balance = forms.DecimalField(max_digits=12, decimal_places=2, required=False)
    country = forms.ChoiceField(choices=Profile.COUNTRIES, required=False)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'is_active', 'is_staff', 'is_superuser']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            try:
                profile = self.instance.profile
                self.fields['full_name'].initial = profile.full_name
                self.fields['balance'].initial = profile.balance
                self.fields['country'].initial = profile.country
            except Profile.DoesNotExist:
                pass

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save()
            # Save profile data
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.full_name = self.cleaned_data['full_name']
            profile.balance = self.cleaned_data['balance']
            profile.country = self.cleaned_data['country']
            profile.save()
        return user

class WalletForm(forms.ModelForm):
    class Meta:
        model = Wallet
        fields = ['currency', 'address', 'label']

class MatchEditForm(forms.ModelForm):
    class Meta:
        model = Match
        fields = [
            'home_team', 'away_team', 'match_date', 'league', 'status',
            'home_score', 'away_score', 'winner',
            'home_odds', 'draw_odds', 'away_odds'
        ]
        widgets = {
            'match_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }
