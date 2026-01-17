#
# This SPEC is for bulding RPM package that contain complete Qubes Template files.
# This includes the VM's root image, patched with all qubes rpms, etc.
#

%{!?template_name: %global template_name %{getenv:TEMPLATE_NAME}}
%{!?template_version: %global template_version %{getenv:TEMPLATE_VERSION}}
%{!?template_timestamp: %global template_timestamp %{getenv:TEMPLATE_TIMESTAMP}}

%define dest_dir /var/lib/qubes/vm-templates/%{template_name}
%define _binaries_in_noarch_packages_terminate_build 0

Name:           qubes-template-%{template_name}
Version:	    %{template_version}
Release:	    %{template_timestamp}
Summary:	    Qubes OS template for %{template_name}
License:	    GPLv3+
URL:		    http://www.qubes-os.org

Source0:	    root.img
Source1:        appmenus
Source2:        template.conf

Requires:	    xdg-utils
Requires(post):	tar
BuildArch:      noarch
Provides:	    qubes-template
Obsoletes:      %{name} > %{template_version}-%{release}


%description
%{summary}.


%build
rm -f root.img.part.*
fallocate -d %{_sourcedir}/qubeized_images/%{template_name}/root.img
tar --sparse --dereference -cf - %{_sourcedir}/qubeized_images/%{template_name}/root.img | split -d -b 1G - root.img.part.
rm -f root.img


%install
rm -rf $RPM_BUILD_ROOT
mkdir -p $RPM_BUILD_ROOT/%{dest_dir}

for i in %{_builddir}/root.img.part.*; do
    mv $i $RPM_BUILD_ROOT/%{dest_dir}/
done

touch $RPM_BUILD_ROOT/%{dest_dir}/root.img # we will create the real file in %post
touch $RPM_BUILD_ROOT/%{dest_dir}/private.img # we will create the real file in %post
touch $RPM_BUILD_ROOT/%{dest_dir}/volatile.img # we will create the real file in %post
touch $RPM_BUILD_ROOT/%{dest_dir}/clean-volatile.img.tar # we will create the real file in %post

mkdir -p $RPM_BUILD_ROOT/%{dest_dir}/apps.templates
mkdir -p $RPM_BUILD_ROOT/%{dest_dir}/apps.tempicons
mkdir -p $RPM_BUILD_ROOT/%{dest_dir}/apps
cp %{SOURCE1}/whitelisted-appmenus.list %{SOURCE1}/vm-whitelisted-appmenus.list $RPM_BUILD_ROOT/%{dest_dir}/
cp %{SOURCE1}/netvm-whitelisted-appmenus.list $RPM_BUILD_ROOT/%{dest_dir}/
cp %{SOURCE2} $RPM_BUILD_ROOT/%{dest_dir}/


%pre

echo "***** ERROR: do not install template using rpm/dnf or similar" >&2
echo "*****        use 'qvm-template install' instead" >&2

exit 1

%clean
rm -rf $RPM_BUILD_ROOT


%files
%defattr(660,root,qubes,770)
%attr(2770,root,qubes) %dir %{dest_dir}

%ghost %{dest_dir}/root.img
%ghost %{dest_dir}/volatile.img
%ghost %{dest_dir}/private.img

%{dest_dir}/root.img.part.*
%{dest_dir}/clean-volatile.img.tar

%attr (775,root,qubes) %dir %{dest_dir}/apps
%attr (775,root,qubes) %dir %{dest_dir}/apps.templates
%attr (775,root,qubes) %dir %{dest_dir}/apps.tempicons
%attr (664,root,qubes) %{dest_dir}/whitelisted-appmenus.list
%attr (664,root,qubes) %{dest_dir}/vm-whitelisted-appmenus.list
%attr (664,root,qubes) %{dest_dir}/netvm-whitelisted-appmenus.list
%attr (664,root,qubes) %{dest_dir}/template.conf
