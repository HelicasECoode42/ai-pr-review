from accounts import load_for_actor

def account_page(account_id, actor):
    account = load_for_actor(account_id, actor)
    return render(account)
